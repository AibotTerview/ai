import boto3
import os
import uuid
from django.conf import settings
from botocore.exceptions import NoCredentialsError

def upload_file_to_s3(file_path: str, content_type: str = "application/octet-stream") -> str | None:
    """
    파일을 S3에 업로드하고 URL을 반환합니다.
    URL 구조: https://{bucket_name}.s3.{region_name}.amazonaws.com/{object_name}
    """
    # 환경 변수가 없으면 업로드 스킵 (개발 중 편의)
    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        print("[S3] AWS Credentials not found. Skipping upload.")
        return None

    s3 = boto3.client(
        's3',
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME
    )

    try:
        file_extension = os.path.splitext(file_path)[1]
        object_name = f"interview/{uuid.uuid4()}{file_extension}"
        
        s3.upload_file(
            file_path, 
            settings.AWS_STORAGE_BUCKET_NAME, 
            object_name,
            ExtraArgs={'ContentType': content_type}
        )

        url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{object_name}"
        print(f"[S3] Upload successful: {url}")
        return url

    except FileNotFoundError:
        print(f"[S3] File not found: {file_path}")
        return None
    except NoCredentialsError:
        print("[S3] Credentials not available")
        return None
    except Exception as e:
        print(f"[S3] Upload failed: {e}")
        return None
