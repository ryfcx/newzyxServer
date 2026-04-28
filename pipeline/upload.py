import boto3
import mimetypes
from botocore.exceptions import NoCredentialsError, ClientError
import os
import time
from newzyx import config
from newzyx import workspace


def _get_client(service):
    return boto3.client(
        service,
        aws_access_key_id=config.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
        region_name=config.AWS_REGION,
    )


def download_object_if_exists(s3_key, dest_path):
    """
    Download s3://bucket/s3_key to dest_path. Returns True if the object existed.
    """
    client = _get_client("s3")
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        client.download_file(config.S3_BUCKET, s3_key, dest_path)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def upload_files(file_list):
    if not file_list:
        print("  No files to upload")
        return []

    client = _get_client("s3")
    uploaded = []
    project_root = os.path.dirname(os.path.dirname(__file__))
    gen_web = os.path.abspath(workspace.generated_website_dir())
    proj_web = os.path.abspath(workspace.project_website_dir())

    def _under(root, path):
        path = os.path.abspath(path)
        root = os.path.abspath(root)
        return path == root or path.startswith(root + os.sep)

    for fpath in file_list:
        if os.path.isabs(fpath):
            full_path = fpath
        else:
            full_path = os.path.join(project_root, fpath)

        if not os.path.isfile(full_path):
            print(f"  Skip (not found): {fpath}")
            continue

        if _under(gen_web, full_path):
            s3_key = os.path.relpath(full_path, gen_web)
        elif _under(proj_web, full_path):
            s3_key = os.path.relpath(full_path, proj_web)
        else:
            s3_key = os.path.basename(fpath)

        extra = {"ACL": "public-read"}
        content_type, _ = mimetypes.guess_type(full_path)
        if content_type:
            extra["ContentType"] = content_type

        try:
            client.upload_file(full_path, config.S3_BUCKET, s3_key, ExtraArgs=extra)
            uploaded.append(s3_key)
            print(f"  Uploaded: {s3_key}")
        except FileNotFoundError:
            print(f"  File not found: {full_path}")
        except NoCredentialsError:
            print("  AWS credentials not available")
        except ClientError as e:
            print(f"  Failed to upload {s3_key}: {e}")

    if uploaded and config.DISTRIBUTION_ID:
        _invalidate_cache()

    print(f"Uploaded {len(uploaded)} files to s3://{config.S3_BUCKET}")
    return uploaded


def _invalidate_cache():
    try:
        client = _get_client("cloudfront")
        ref = str(time.time()).replace(".", "")
        inv = client.create_invalidation(
            DistributionId=config.DISTRIBUTION_ID,
            InvalidationBatch={
                "Paths": {"Quantity": 1, "Items": ["/*"]},
                "CallerReference": ref,
            },
        )
        print(f"  CloudFront invalidation: {inv['Invalidation']['Id']}")
    except Exception as e:
        print(f"  CloudFront invalidation failed: {e}")
