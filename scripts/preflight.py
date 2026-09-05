"""Read-only WordPress configuration check. Never prints credentials."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config import load_config
from wordpress_api import WordPressAPI
def main():
    cfg=load_config()
    wp=WordPressAPI(cfg.wp_url,cfg.wp_username,cfg.wp_app_password)
    result={"site":cfg.wp_url}
    try:
        user=wp.request("GET","wp/v2/users/me")
        result["authenticated"]=bool(user.get("id"))
        terms=wp.request("GET","wp/v2/categories",params={"per_page":100})
        result["categories"]=[{"id":t["id"],"name":t["name"],"count":t["count"]} for t in terms]
        try:
            result["companion"]=wp.request("GET","ms-news/v1/health")
        except Exception as exc:
            result["companion"]="not available: "+type(exc).__name__
    except Exception as exc:
        result["error"]=type(exc).__name__
    print(json.dumps(result,indent=2))
if __name__=="__main__":
    main()
