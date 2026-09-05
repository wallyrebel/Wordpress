<?php
/**
 * Plugin Name: MS News Workflow
 * Description: Authenticated publishing receipts with required images and taxonomy.
 * Version: 1.0.1
 */
if (!defined('ABSPATH')) { exit; }

function msn_permission() { return current_user_can('edit_others_posts'); }
function msn_key_ok($key) { return is_string($key) && preg_match('/^[a-f0-9]{64}$/D', $key); }
function msn_receipt($key) {
    return get_option('msn_receipt_' . $key, array('versions' => array()));
}
add_action('rest_api_init', function () {
    register_rest_route('ms-news/v1', '/health', array(
        'methods' => 'GET', 'permission_callback' => 'msn_permission',
        'callback' => function () { return array('version' => 1, 'min_image_width' => 600, 'min_image_height' => 400); }
    ));
    register_rest_route('ms-news/v1', '/receipt/(?P<key>[a-f0-9]{64})', array(
        'methods' => 'GET', 'permission_callback' => 'msn_permission',
        'callback' => function ($r) { return msn_receipt($r['key']); }
    ));
    register_rest_route('ms-news/v1', '/article', array(
        'methods' => 'POST', 'permission_callback' => 'msn_permission',
        'callback' => 'msn_article'
    ));
});

function msn_article($request) {
    $p = $request->get_json_params();
    $key = isset($p['source_key']) ? $p['source_key'] : '';
    $hash = isset($p['content_hash']) ? $p['content_hash'] : '';
    if (!msn_key_ok($key) || !msn_key_ok($hash)) {
        return new WP_Error('invalid_key', 'Invalid source or version key.', array('status' => 400));
    }
    $source = esc_url_raw(isset($p['source_url']) ? $p['source_url'] : '', array('http', 'https'));
    if (!$source) { return new WP_Error('invalid_source', 'Source URL required.', array('status' => 400)); }
    // add_option is an atomic unique insert. Never auto-expire a lock: a timed-out
    // client may still have a live server request. An abandoned lock needs inspection.
    $lock = 'msn_lock_' . $key;
    if (!add_option($lock, gmdate('c'), '', false)) {
        return new WP_Error('source_locked', 'Source is processing or needs lock recovery.', array('status' => 409));
    }
    $release = true;
    try {
        $receipt = msn_receipt($key);
        if (isset($receipt['versions'][$hash])) { return $receipt['versions'][$hash]; }
        if (!empty($p['adopt_post_id'])) {
            $id = absint($p['adopt_post_id']);
            if (!get_post($id) || get_post_type($id) !== 'post' || !current_user_can('edit_post', $id)) {
                return new WP_Error('invalid_legacy', 'Cannot adopt legacy post.', array('status' => 400));
            }
        } else {
            $title = sanitize_text_field(isset($p['title']) ? $p['title'] : '');
            $content = wp_kses_post(isset($p['content']) ? $p['content'] : '');
            if (!$title || !trim(wp_strip_all_tags($content))) {
                return new WP_Error('empty_article', 'Article body and title required.', array('status' => 400));
            }
            if (!empty($receipt['post_id'])) {
                return new WP_Error('source_update', 'Source changed; original preserved for review.', array('status' => 409));
            }
            if (!isset($p['status']) || $p['status'] !== 'publish') {
                return new WP_Error('publish_only', 'Only checked publication is supported.', array('status' => 400));
            }
            $verification = isset($p['evidence']['verification']) ? $p['evidence']['verification'] : array();
            if (empty($verification['supported']) || !empty($verification['issues']) || empty($p['evidence']['extraction']['facts'])) {
                return new WP_Error('evidence_required', 'Passed factual verification and source evidence required.', array('status' => 400));
            }
            if (!current_user_can('publish_posts')) {
                return new WP_Error('cannot_publish', 'Publishing permission required.', array('status' => 403));
            }
            $categories = isset($p['categories']) ? array_map('absint', (array)$p['categories']) : array();
            $tags = isset($p['tags']) ? array_map('absint', (array)$p['tags']) : array();
            if (!$categories || !$tags || !empty($p['review_reasons'])) {
                return new WP_Error('checks_failed', 'Category, tags and passed checks required.', array('status' => 400));
            }
            foreach ($categories as $term) {
                if (!term_exists($term, 'category')) { return new WP_Error('bad_category', 'Unknown category.', array('status' => 400)); }
            }
            foreach ($tags as $term) {
                if (!term_exists($term, 'post_tag')) { return new WP_Error('bad_tag', 'Unknown tag.', array('status' => 400)); }
            }
            $media = isset($p['featured_media']) ? absint($p['featured_media']) : 0;
            $dimensions = wp_get_attachment_metadata($media);
            if (!$media || !wp_attachment_is_image($media) || empty($dimensions['width']) || empty($dimensions['height'])
                || $dimensions['width'] < 600 || $dimensions['height'] < 400 || $dimensions['width'] / $dimensions['height'] > 3.5) {
                return new WP_Error('bad_image', 'Invalid image.', array('status' => 400));
            }
            // From here failures keep the lock, preventing ambiguous writes being retried.
            $release = false;
            $id = wp_insert_post(wp_slash(array(
                'post_type' => 'post', 'post_status' => 'publish', 'post_title' => $title,
                'post_content' => $content, 'post_excerpt' => sanitize_text_field(isset($p['excerpt']) ? $p['excerpt'] : ''),
                'post_category' => $categories, 'tags_input' => $tags,
                'meta_input' => array('_thumbnail_id' => $media, '_msn_source_key' => $key, '_msn_content_hash' => $hash,
                    '_msn_source_url' => $source,
                    '_msn_original_post' => isset($receipt['post_id']) ? $receipt['post_id'] : 0,
                    '_msn_source_published' => sanitize_text_field(isset($p['source_published']) ? $p['source_published'] : ''),
                    '_msn_review_reasons' => array_map('sanitize_text_field', (array)(isset($p['review_reasons']) ? $p['review_reasons'] : array())),
                    '_msn_evidence' => wp_json_encode(isset($p['evidence']) ? $p['evidence'] : array()))
            )), true);
            if (is_wp_error($id)) { return $id; }
            $result = array('post_id' => $id, 'status' => 'publish', 'url' => get_permalink($id));
            if (empty($receipt['post_id'])) { $receipt['post_id'] = $id; }
            $receipt['versions'][$hash] = $result;
            if (!update_option('msn_receipt_' . $key, $receipt, false)) {
                return new WP_Error('receipt_failed', 'Post saved; receipt requires recovery.', array('status' => 500));
            }
        }
        $result = array('post_id' => $id, 'status' => get_post_status($id),
            'url' => get_post_status($id) === 'publish' ? get_permalink($id) : get_edit_post_link($id, 'raw'));
        if (empty($receipt['post_id'])) { $receipt['post_id'] = $id; }
        $receipt['versions'][$hash] = $result;
        update_option('msn_receipt_' . $key, $receipt, false);
        $release = true;
        return $result;
    } finally {
        if ($release) { delete_option($lock); }
    }
}

add_action('add_meta_boxes', function () {
    add_meta_box('msn-evidence', 'News workflow: source and editorial review', function ($post) {
        $source = get_post_meta($post->ID, '_msn_source_url', true);
        if (!$source) { echo '<p>No workflow record.</p>'; return; }
        echo '<p>Source: <a href="' . esc_url($source) . '" target="_blank" rel="noopener noreferrer">'
            . esc_html($source) . '</a></p>';
        $original = absint(get_post_meta($post->ID, '_msn_original_post', true));
        if ($original) {
            echo '<p><strong>Source update: compare with the original article before applying a correction.</strong> '
                . '<a href="' . esc_url(get_edit_post_link($original)) . '">Open original</a></p>';
        }
        foreach ((array)get_post_meta($post->ID, '_msn_review_reasons', true) as $reason) {
            echo '<p>' . esc_html($reason) . '</p>';
        }
        echo '<details><summary>Evidence, verification and token usage</summary><pre style="white-space:pre-wrap">'
            . esc_html(get_post_meta($post->ID, '_msn_evidence', true)) . '</pre></details>';
    }, 'post', 'normal');
});
