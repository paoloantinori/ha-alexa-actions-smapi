#!/usr/bin/env bash
set -euo pipefail

# Build script that packages the Lambda code into a deployable zip.
# Output: dist/lambda_package.zip

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LAMBDA_DIR="${PROJECT_ROOT}/lambda"
DIST_DIR="${PROJECT_ROOT}/dist"
PACKAGE_DIR="${DIST_DIR}/package"

echo "=== Building Lambda deployment package ==="

# Clean previous build
rm -rf "${DIST_DIR}"
mkdir -p "${PACKAGE_DIR}"

# Install dependencies
echo "Installing Python dependencies..."
pip install --target "${PACKAGE_DIR}" -r "${LAMBDA_DIR}/requirements.txt" --quiet

# Copy Lambda source files
echo "Copying Lambda source files..."
cp "${LAMBDA_DIR}"/*.py "${PACKAGE_DIR}/"
cp "${LAMBDA_DIR}/language_strings.json" "${PACKAGE_DIR}/"

# Create deployment zip
echo "Creating deployment zip..."
cd "${PACKAGE_DIR}"
zip -r "${DIST_DIR}/lambda_package.zip" . -q
cd "${PROJECT_ROOT}"

# Report
SIZE=$(du -h "${DIST_DIR}/lambda_package.zip" | cut -f1)
echo "=== Build complete: dist/lambda_package.zip (${SIZE}) ==="
