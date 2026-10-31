#!/usr/bin/env python3
"""
Upload project files to S3 for EC2 deployment.
Run this from your LOCAL machine before deploying to EC2.

Usage:
    python scripts/upload_project_to_s3.py --bucket YOUR_BUCKET_NAME
"""

import boto3
import os
import zipfile
import argparse
import sys
from pathlib import Path

# Files/folders to EXCLUDE from the upload
EXCLUDE_PATTERNS = {
    'venv', '__pycache__', '.pytest_cache', '.git',
    '.kiro', 'data/chromadb', 'deployment_info.json',
    '.bedrock_agentcore'
}

def create_project_zip(output_path: str = "cloudops-project.zip") -> str:
    """Create a zip of the project, excluding unnecessary files."""
    project_root = Path(__file__).parent.parent
    zip_path = project_root / output_path

    print(f"Creating project zip: {zip_path}")

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_path in project_root.rglob('*'):
            # Skip excluded patterns
            parts = set(file_path.parts)
            if any(excl in str(file_path) for excl in EXCLUDE_PATTERNS):
                continue
            if file_path.is_file():
                arcname = file_path.relative_to(project_root)
                zf.write(file_path, arcname)
                print(f"  + {arcname}")

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"\n✅ Created {output_path} ({size_mb:.1f} MB)")
    return str(zip_path)


def upload_to_s3(zip_path: str, bucket_name: str, region: str = 'us-east-1') -> str:
    """Upload the zip file to S3."""
    s3 = boto3.client('s3', region_name=region)
    s3_key = 'cloudops-project.zip'

    print(f"\nUploading to s3://{bucket_name}/{s3_key} ...")

    s3.upload_file(
        zip_path, bucket_name, s3_key,
        ExtraArgs={'ServerSideEncryption': 'AES256'}
    )

    s3_uri = f"s3://{bucket_name}/{s3_key}"
    print(f"✅ Uploaded to {s3_uri}")
    return s3_uri


def main():
    parser = argparse.ArgumentParser(description='Upload CloudOps project to S3')
    parser.add_argument('--bucket', required=True, help='S3 bucket name')
    parser.add_argument('--region', default='us-east-1', help='AWS region')
    args = parser.parse_args()

    # Create zip
    zip_path = create_project_zip()

    # Upload to S3
    s3_uri = upload_to_s3(zip_path, args.bucket, args.region)

    print(f"""
============================================================
✅ Project uploaded successfully!

Next step — on your EC2 instance, run:
  aws s3 cp {s3_uri} /opt/cloudops/cloudops-project.zip
  cd /opt/cloudops && unzip -q cloudops-project.zip
  bash scripts/deploy_to_ec2.sh
============================================================
""")

    # Clean up local zip
    os.remove(zip_path)
    print("Cleaned up local zip file.")


if __name__ == "__main__":
    main()
