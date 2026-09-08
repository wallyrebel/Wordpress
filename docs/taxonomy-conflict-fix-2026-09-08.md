# Repeated WordPress tag conflict

Runs 11121 (34271767427) and 11122 (34274266218) each published one article and read all 155 feeds. Each finished with one error on “Conference Schedule Announced For MSU Baseball” at the WordPress category/tag stage.

Reproduced with the validated article: searching for `texas a&m` did not find the existing tag. Creating it returned HTTP 400, `term_exists`, existing term ID 592. The client previously treated this expected collision as an unrecoverable item error, so it repeated on the next run.

The client now handles only `term_exists` on HTTP 400/409, verifies the positive integer ID through the tags endpoint, and reuses it. It does not retry the write or suppress permission/server errors. Logs and review artifacts now include HTTP status and API error code without request headers, URLs or response messages.

Validation: 96 offline regression tests passed. The same live taxonomy operation succeeds and returns category 5605 and tag IDs 110, 559, 120, 592 and 8603. No article was published during this diagnostic; the scheduled workflow retains its existing publication gates and source receipts.
