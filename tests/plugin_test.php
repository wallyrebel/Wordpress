<?php
// Contract tests with WordPress API stubs. Actual site integration is a separate check.
define('ABSPATH', __DIR__);
$options = array(); $posts = array(); $next_id = 100; $inserts = 0;
$image_dimensions = array('width'=>720, 'height'=>960);
function add_action(...$args) {}
function current_user_can(...$args) { return true; }
function get_option($key,$default=false) { global $options; return $options[$key] ?? $default; }
function add_option($key,$value,...$args) { global $options; if(isset($options[$key])) return false; $options[$key]=$value; return true; }
function update_option($key,$value,...$args) { global $options; if(isset($options[$key]) && $options[$key] === $value) return false; $options[$key]=$value; return true; }
function delete_option($key) { global $options; unset($options[$key]); }
function esc_url_raw($value,...$args) { return filter_var($value,FILTER_VALIDATE_URL) ? $value : ''; }
function sanitize_text_field($value) { return strip_tags($value); }
function wp_kses_post($value) { return strip_tags($value,'<p><a>'); }
function wp_strip_all_tags($value) { return strip_tags($value); }
function absint($value) { return abs((int)$value); }
function term_exists($id,$type) { return $id > 0; }
function wp_attachment_is_image($id) { return $id === 3; }
function wp_get_attachment_metadata($id) { global $image_dimensions; return $id === 3 ? $image_dimensions : array(); }
function wp_json_encode($value) { return json_encode($value); }
function wp_slash($value) { return $value; }
class WP_Error {
    public function __construct(public $code, public $message, public $data=array()) {}
}
function is_wp_error($value) { return $value instanceof WP_Error; }
function wp_insert_post($post,$error=false) {
    global $posts,$next_id,$inserts;
    ++$inserts; $id=++$next_id; $posts[$id]=$post; return $id;
}
function get_post($id) { global $posts; return $posts[$id] ?? null; }
function get_post_type($id) { return 'post'; }
function get_post_status($id) { global $posts; return $posts[$id]['post_status']; }
function get_permalink($id) { return 'https://example.org/?p='.$id; }
function get_edit_post_link($id,...$args) { return 'https://example.org/edit/'.$id; }
class Request {
    public function __construct(public $payload) {}
    public function get_json_params() { return $this->payload; }
}
require __DIR__.'/../wordpress-plugin/ms-news-workflow/ms-news-workflow.php';
function check($condition,$message) {
    if (!$condition) { fwrite(STDERR,"FAIL: ".$message."\n"); exit(1); }
    echo "PASS: ".$message."\n";
}
$p=array('source_key'=>str_repeat('a',64),'content_hash'=>str_repeat('b',64),
    'source_url'=>'https://example.org/story','title'=>'News','content'=>'<p>Verified story.</p>',
    'status'=>'publish','categories'=>array(1),'tags'=>array(2),'featured_media'=>3,'review_reasons'=>array(),
    'evidence'=>array('verification'=>array('supported'=>true,'issues'=>array()),'extraction'=>array('facts'=>array('test fact'))));
$first=msn_article(new Request($p));
check(!is_wp_error($first) && $first['status']==='publish','complete item publishes');
check($posts[$first['post_id']]['meta_input']['_thumbnail_id']===3,'featured image attached at creation');
$again=msn_article(new Request($p));
check($again['post_id']===$first['post_id'] && $inserts===1,'retry returns durable receipt without duplicate');
$p['content_hash']=str_repeat('c',64);
$changed=msn_article(new Request($p));
check(is_wp_error($changed) && $changed->code==='source_update' && $inserts===1,'correction held, no draft or overwrite');
$p['source_key']=str_repeat('d',64);
$p['featured_media']=0;
check(is_wp_error(msn_article(new Request($p))) && $inserts===1,'missing image blocks publication');
$p['featured_media']=3; $p['tags']=array();
check(is_wp_error(msn_article(new Request($p))) && $inserts===1,'missing tags blocks publication');
$p['tags']=array(2); $p['categories']=array();
check(is_wp_error(msn_article(new Request($p))) && $inserts===1,'missing category blocks publication');
$p['categories']=array(1); $p['status']='draft';
check(is_wp_error(msn_article(new Request($p))) && $inserts===1,'draft creation rejected');
$p['status']='publish'; $p['review_reasons']=array('Needs review');
check(is_wp_error(msn_article(new Request($p))) && $inserts===1,'failed checks block publication');
$p['review_reasons']=array();
$image_dimensions = array('width'=>300, 'height'=>200);
check(is_wp_error(msn_article(new Request($p))) && $inserts===1,'small thumbnails still block publication');
$image_dimensions = array('width'=>1200, 'height'=>100);
check(is_wp_error(msn_article(new Request($p))) && $inserts===1,'banner-shaped images still block publication');
$image_dimensions = array('width'=>720, 'height'=>960);
add_option('msn_lock_'.$p['source_key'],'busy');
$locked=msn_article(new Request($p));
check(is_wp_error($locked) && $locked->code==='source_locked' && $inserts===1,'concurrent/abandoned lock fails closed');
