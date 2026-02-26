import boto3
from django.conf import settings

S3_INTERVIEW_PREFIX = "interviews"


class S3MultipartUpload:
    MIN_PART_SIZE = 5 * 1024 * 1024  # 5MB (S3 최소 파트 크기)

    def __init__(self, key: str):
        self._s3 = boto3.client(
            "s3",
            region_name=settings.AWS_S3_REGION_NAME,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
        self._bucket = settings.AWS_STORAGE_BUCKET_NAME
        self._key = key
        self._upload_id: str | None = None
        self._parts: list[dict] = []
        self._part_number = 1
        self._buf = b""

    def start(self) -> None:
        resp = self._s3.create_multipart_upload(
            Bucket=self._bucket, Key=self._key, ContentType="video/mp4"
        )
        self._upload_id = resp["UploadId"]

    def write(self, data: bytes) -> None:
        self._buf += data
        while len(self._buf) >= self.MIN_PART_SIZE:
            self._flush_part(self._buf[: self.MIN_PART_SIZE])
            self._buf = self._buf[self.MIN_PART_SIZE :]

    def _flush_part(self, data: bytes) -> None:
        resp = self._s3.upload_part(
            Bucket=self._bucket,
            Key=self._key,
            UploadId=self._upload_id,
            PartNumber=self._part_number,
            Body=data,
        )
        self._parts.append({"PartNumber": self._part_number, "ETag": resp["ETag"]})
        self._part_number += 1

    def complete(self) -> str:
        """남은 버퍼 업로드 후 완료. S3 URL 반환."""
        if self._buf:
            self._flush_part(self._buf)
            self._buf = b""
        if not self._parts:
            self.abort()
            raise RuntimeError("S3MultipartUpload: 업로드할 데이터가 없습니다 (parts=0)")
        self._s3.complete_multipart_upload(
            Bucket=self._bucket,
            Key=self._key,
            UploadId=self._upload_id,
            MultipartUpload={"Parts": self._parts},
        )
        return f"https://{self._bucket}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{self._key}"

    def abort(self) -> None:
        if self._upload_id:
            self._s3.abort_multipart_upload(
                Bucket=self._bucket, Key=self._key, UploadId=self._upload_id
            )
