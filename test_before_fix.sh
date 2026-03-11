#!/bin/bash
# BEFORE FIX TEST: Demonstrates the AssertionError bug
# This test SHOULD fail, proving the bug exists

set -e

echo "🐛 BEFORE FIX TEST: Demonstrating the Bug"
echo "=========================================="
echo "This test SHOULD show the AssertionError to prove the bug exists."
echo ""

# Build UNFIXED image from current code
echo "📦 Building UNFIXED image (current code)..."
BUILD_DATE_VALUE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker build --build-arg BUILD_DATE="$BUILD_DATE_VALUE" -t wyze-bridge-unfixed:latest -f docker/Dockerfile . 2>&1 | tail -3

# Run container with real credentials
echo ""
echo "🚀 Starting container with REAL credentials..."
docker run -d \
  --name wyze-bridge-unfixed \
  -p 8556:8554 \
  -p 8890:8888 \
  -p 5052:5000 \
  -e WYZE_EMAIL='user@example.com' \
  -e WYZE_PASSWORD='REDACTED_WYZE_PASSWORD' \
  -e API_ID='REDACTED_API_ID' \
  -e API_KEY='REDACTED_API_KEY' \
  -e ENABLE_AUDIO=true \
  -e ON_DEMAND=true \
  -e FILTER_NAMES='GARAGE, DOG RUN, NORTH YARD, DECK, Hamster' \
  -e LLHLS=true \
  wyze-bridge-unfixed:latest 2>&1 | tail -1

echo ""
echo "⏳ Waiting 60 seconds for cameras to connect..."
sleep 60

echo ""
echo "🔍 Checking logs for cameras..."
docker logs wyze-bridge-unfixed 2>&1 | grep -E "(Connecting|stream is UP|Timed out)" | tail -10

echo ""
echo "💥 Triggering shutdown to reproduce the bug..."
docker stop wyze-bridge-unfixed -t 30 2>&1 | tail -1
sleep 5

echo ""
echo "🔍 Checking for AssertionError (this SHOULD appear in unfixed version)..."
LOGS=$(docker logs wyze-bridge-unfixed 2>&1)
if echo "$LOGS" | grep -q "can only test a child process"; then
  echo "✅ BUG CONFIRMED: Found AssertionError"
  echo "   This proves the bug exists and needs fixing."
  echo ""
  echo "Exception details:"
  echo "$LOGS" | grep -A5 "can only test a child process" | head -10
else
  echo "⚠️  NOTE: AssertionError not found in this run"
  echo "   (The bug is intermittent - may need multiple runs)"
fi

echo ""
echo "🧹 Cleaning up..."
docker rm wyze-bridge-unfixed 2>/dev/null || true

echo ""
echo "=========================================="
echo "If you saw 'can only test a child process' above, the bug is confirmed."
echo "Proceeding to apply fixes..."
echo ""
