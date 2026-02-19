import logging
from django.conf import settings
import boto3

logger = logging.getLogger(__name__)

def upload_file(local_path: str, key: str) -> str | None:

    bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", None)
    region = getattr(settings, "AWS_S3_REGION_NAME", "ap-northeast-2")
    client = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )
    with open(local_path, "rb") as f:
        client.upload_fileobj(f, bucket, key, ExtraArgs={"ContentType": "video/mp4"})

    url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    return url
