import argparse
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from dotenv import load_dotenv

CREDENTIAL_PATH = Path.home() / ".insighta" / "credentials.json"


def read_credentials():
	if not CREDENTIAL_PATH.exists():
		return {}
	return json.loads(CREDENTIAL_PATH.read_text(encoding="utf-8"))


def write_credentials(data):
	CREDENTIAL_PATH.parent.mkdir(parents=True, exist_ok=True)
	CREDENTIAL_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def request_json(method, url, body=None, token=None):
	data = json.dumps(body).encode("utf-8") if body is not None else None
	request = urllib.request.Request(url, data=data, method=method)
	request.add_header("Accept", "application/json")
	if body is not None:
		request.add_header("Content-Type", "application/json")
	if token:
		request.add_header("Authorization", f"Bearer {token}")
	with urllib.request.urlopen(request, timeout=30) as response:
		payload = response.read().decode("utf-8")
		return json.loads(payload) if payload else {}


def api_base(args):
	return args.api.rstrip("/")


def refresh_if_needed(args, creds):
	try:
		return request_json("GET", f"{api_base(args)}/me", token=creds.get("access_token"))
	except urllib.error.HTTPError as exc:
		if exc.code != 401 or not creds.get("refresh_token"):
			raise
	data = request_json("POST", f"{api_base(args)}/auth/refresh", {"refresh_token": creds["refresh_token"]})
	creds.update({
		"access_token": data["access_token"],
		"refresh_token": data["refresh_token"],
	})
	write_credentials(creds)
	return request_json("GET", f"{api_base(args)}/me", token=creds.get("access_token"))


def command_login(args):
	callback_result = {}

	class CallbackHandler(BaseHTTPRequestHandler):
		def log_message(self, *_):
			return

		def do_GET(self):
			query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
			callback_result["code"] = query.get("code", [""])[0]
			callback_result["state"] = query.get("state", [""])[0]
			self.send_response(200)
			self.end_headers()
			self.wfile.write(b"Insighta login complete. You can close this window.")

	server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
	redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
	start_url = f"{api_base(args)}/auth/github/start?" + urllib.parse.urlencode({
		"client": "cli",
		"redirect_uri": redirect_uri,
	})
	start = request_json("GET", start_url)
	thread = threading.Thread(target=server.handle_request, daemon=True)
	thread.start()
	print("Opening GitHub OAuth in your browser...")
	webbrowser.open(start["authorize_url"])
	thread.join(timeout=180)
	server.server_close()
	if not callback_result.get("code"):
		raise SystemExit("Login timed out before GitHub returned a code.")
	callback_url = f"{api_base(args)}/auth/github/callback?" + urllib.parse.urlencode({
		"code": callback_result["code"],
		"state": callback_result["state"],
		"code_verifier": start["code_verifier"],
	})
	tokens = request_json("GET", callback_url)
	write_credentials({
		"api": api_base(args),
		"access_token": tokens["access_token"],
		"refresh_token": tokens["refresh_token"],
		"token_type": tokens["token_type"],
		"role": tokens["role"],
	})
	print(f"Logged in as {tokens['role']}. Credentials saved to {CREDENTIAL_PATH}")


def command_me(args):
	creds = read_credentials()
	data = refresh_if_needed(args, creds)
	print(json.dumps(data, indent=2))


def command_profiles(args):
	creds = read_credentials()
	refresh_if_needed(args, creds)
	params = {key: value for key, value in vars(args).items() if key in {
		"gender", "age_group", "country_id", "min_age", "max_age", "sort_by", "order", "page", "limit"
	} and value is not None}
	url = f"{api_base(args)}/profiles"
	if params:
		url += "?" + urllib.parse.urlencode(params)
	print(json.dumps(request_json("GET", url, token=creds["access_token"]), indent=2))


def command_search(args):
	creds = read_credentials()
	refresh_if_needed(args, creds)
	url = f"{api_base(args)}/profiles/search?" + urllib.parse.urlencode({"q": args.query, "page": args.page, "limit": args.limit})
	print(json.dumps(request_json("GET", url, token=creds["access_token"]), indent=2))


def command_export(args):
	creds = read_credentials()
	refresh_if_needed(args, creds)
	request = urllib.request.Request(f"{api_base(args)}/profiles/export")
	request.add_header("Authorization", f"Bearer {creds['access_token']}")
	with urllib.request.urlopen(request, timeout=60) as response:
		Path(args.output).write_bytes(response.read())
	print(f"Exported CSV to {args.output}")


def command_logout(args):
	creds = read_credentials()
	if creds.get("refresh_token"):
		try:
			request_json("POST", f"{api_base(args)}/auth/logout", {"refresh_token": creds["refresh_token"]})
		except Exception:
			pass
	if CREDENTIAL_PATH.exists():
		CREDENTIAL_PATH.unlink()
	print("Logged out.")


def build_parser():
	parser = argparse.ArgumentParser(prog="insighta")
	parser.add_argument("--api", default=os.environ.get("INSIGHTA_API", "http://localhost:8000/api/v1"))
	sub = parser.add_subparsers(required=True)
	login = sub.add_parser("login")
	login.set_defaults(func=command_login)
	me = sub.add_parser("me")
	me.set_defaults(func=command_me)
	profiles = sub.add_parser("profiles")
	for name in ["gender", "age_group", "country_id", "sort_by", "order"]:
		profiles.add_argument(f"--{name}")
	for name in ["min_age", "max_age", "page", "limit"]:
		profiles.add_argument(f"--{name}", type=int)
	profiles.set_defaults(func=command_profiles)
	search = sub.add_parser("search")
	search.add_argument("query")
	search.add_argument("--page", type=int, default=1)
	search.add_argument("--limit", type=int, default=10)
	search.set_defaults(func=command_search)
	export = sub.add_parser("export")
	export.add_argument("--output", default="insighta_profiles.csv")
	export.set_defaults(func=command_export)
	logout = sub.add_parser("logout")
	logout.set_defaults(func=command_logout)
	return parser


def main(argv=None):
	parser = build_parser()
	args = parser.parse_args(argv)
	try:
		args.func(args)
	except urllib.error.HTTPError as exc:
		sys.stderr.write(exc.read().decode("utf-8") + "\n")
		raise SystemExit(exc.code)


if __name__ == "__main__":
	main()
