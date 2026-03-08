#!/usr/bin/env bash

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}Running rust-ocr-worker tests...${NC}"
if cd rust-ocr-worker && cargo test; then
    echo -e "${GREEN}rust-ocr-worker tests passed!${NC}\n"
    cd ..
else
    echo -e "${RED}rust-ocr-worker tests failed!${NC}"
    exit 1
fi

echo -e "${BLUE}Running rust-core tests...${NC}"
if cd rust-core && LD_LIBRARY_PATH=$(python3 -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))"):$LD_LIBRARY_PATH cargo test --no-default-features; then
    echo -e "${GREEN}rust-core tests passed!${NC}\n"
    cd ..
else
    echo -e "${RED}rust-core tests failed!${NC}"
    exit 1
fi

echo -e "${BLUE}Running JavaScript tests...${NC}"
if npm test; then
    echo -e "${GREEN}JavaScript tests passed!${NC}\n"
else
    echo -e "${RED}JavaScript tests failed!${NC}"
    exit 1
fi

echo -e "${BLUE}Running riemann-ai backend tests...${NC}"
if cd riemann-ai && conda run -n rmai python3 -m pytest; then
    echo -e "${GREEN}riemann-ai tests passed!${NC}\n"
    cd ..
else
    echo -e "${RED}riemann-ai tests failed!${NC}"
    exit 1
fi

echo -e "${BLUE}Running python-app tests...${NC}"
if cd python-app && python3 -m pytest; then
    echo -e "${GREEN}python-app tests passed!${NC}\n"
    cd ..
else
    echo -e "${RED}python-app tests failed!${NC}"
    exit 1
fi

echo -e "${GREEN}All test suites executed successfully!${NC}"