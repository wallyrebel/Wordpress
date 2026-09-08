import json
import unittest
from unittest.mock import Mock
import requests
from wordpress_api import WordPressAPI, safe_http_details

def http_error(status=400, code="term_exists", term_id=592):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({"code":code,"message":"Private response text",
        "data":{"status":status,"term_id":term_id}}).encode()
    return requests.HTTPError("Private request URL", response=response)

class TaxonomyConflictTests(unittest.TestCase):
    def setUp(self):
        self.wp=WordPressAPI("https://msnewsgroup.com", "test", "test")

    def test_ampersand_name_missing_from_search_reuses_existing_tag(self):
        self.wp.request=Mock(side_effect=[{"id":5605}, [], http_error(), {"id":592,"name":"Texas A&amp;M"}])
        self.assertEqual(self.wp.taxonomy("Sports",["texas a&m"],{"Sports":5605}),([5605],[592]))
        self.wp.request.assert_called_with("GET","wp/v2/tags/592")
        self.assertEqual(sum(c.args[0]=="POST" for c in self.wp.request.call_args_list),1)

    def test_concurrent_creation_conflict_reuses_verified_id(self):
        self.wp.request=Mock(side_effect=[http_error(409),{"id":592}])
        self.assertEqual(self.wp.create_or_get_tag("Texas A&M"),592)

    def test_unrelated_http_failures_are_not_swallowed(self):
        for status,code in [(403,"rest_cannot_create"),(500,"term_exists"),(400,"rest_invalid_param")]:
            with self.subTest(status=status,code=code):
                self.wp.request=Mock(side_effect=http_error(status,code))
                with self.assertRaises(requests.HTTPError):self.wp.create_or_get_tag("Example")
                self.assertEqual(self.wp.request.call_count,1)

    def test_invalid_conflict_ids_do_not_trigger_lookup(self):
        for term_id in [None,0,-1,True,"592","../users"]:
            with self.subTest(term_id=term_id):
                self.wp.request=Mock(side_effect=http_error(term_id=term_id))
                with self.assertRaises(requests.HTTPError):self.wp.create_or_get_tag("Example")
                self.assertEqual(self.wp.request.call_count,1)

    def test_failed_verification_does_not_publish_with_wrong_tag(self):
        self.wp.request=Mock(side_effect=[http_error(),{"id":100}])
        with self.assertRaises(ValueError):self.wp.create_or_get_tag("Example")

    def test_non_json_conflict_is_not_swallowed(self):
        exc=http_error();exc.response._content=b"<html>Proxy error</html>"
        self.wp.request=Mock(side_effect=exc)
        with self.assertRaises(requests.HTTPError):self.wp.create_or_get_tag("Example")

    def test_diagnostics_exclude_sensitive_request_and_response_text(self):
        self.assertEqual(safe_http_details(http_error()),{"http_status":400,"api_error_code":"term_exists"})
        self.assertEqual(safe_http_details(ValueError("secret")),{})
        self.assertEqual(safe_http_details(http_error(code="<html>secret</html>")),{"http_status":400})

if __name__=="__main__":unittest.main()
