import os
import secrets
import string

import boto3
import yaml
from botocore.exceptions import ClientError
from flask import Flask, Response, jsonify, request


CONFIG_PATH = os.environ.get("APP_CONFIG", "config.yaml")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

HOST = cfg.get("host", "0.0.0.0")
PORT = int(cfg.get("port", 5000))
BASE_URL = cfg.get("base_url", "http://files.local").rstrip("/")
MAX_MB = int(cfg.get("max_upload_mb", 10))
TOKEN_LEN = int(cfg.get("token_length", 5))

S3_ENDPOINT = cfg.get("s3_endpoint")
S3_REGION = cfg.get("s3_region", "us-east-1")
S3_BUCKET = cfg.get("s3_bucket")
S3_VERIFY_SSL = cfg.get("s3_verify_ssl", False)

S3_KEY = os.environ.get("S3_ACCESS_KEY")
S3_SECRET = os.environ.get("S3_SECRET_KEY")

if not S3_BUCKET:
    raise ValueError("s3_bucket is required in config.yaml")

s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    region_name=S3_REGION,
    aws_access_key_id=S3_KEY,
    aws_secret_access_key=S3_SECRET,
    verify=S3_VERIFY_SSL,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024


def make_token():
    chars = string.ascii_lowercase + string.digits
    for _ in range(100):
        token = "".join(secrets.choice(chars) for _ in range(TOKEN_LEN))
        try:
            s3.head_object(Bucket=S3_BUCKET, Key=token)
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey", "NotFound"):
                return token
    raise RuntimeError("could not make unique token")


def uploaded_extension(uploaded_name):
    name = os.path.basename(uploaded_name or "")
    name = name.replace("\r", "_").replace("\n", "_").replace('"', "_")
    _, extension = os.path.splitext(name)
    return extension


@app.route("/", methods=["GET"])
def home():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    return Response(html, mimetype="text/html")


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "no file sent"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "no file selected"}), 400

    token = make_token()
    extension = uploaded_extension(f.filename)
    data = f.read()

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=token,
        Body=data,
        Metadata={"original-extension": extension},
    )

    return jsonify({"token": token, "url": f"{BASE_URL}:30080/{token}"})


@app.route("/<token>", methods=["GET"])
def download(token):
    if len(token) != TOKEN_LEN:
        return jsonify({"error": "not found"}), 404

    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=token)
    except ClientError:
        return jsonify({"error": "not found"}), 404

    body = obj["Body"].read()
    extension = obj.get("Metadata", {}).get("original-extension", "")
    filename = f"{token}{extension}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(body, mimetype="application/octet-stream", headers=headers)


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "file too large"}), 413


if __name__ == "__main__":
    app.run(host=HOST, port=PORT)
