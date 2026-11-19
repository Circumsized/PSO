BUILD_DIR ?= build
CMAKE ?= cmake

.PHONY: all configure build test clean

all: build

configure:
	$(CMAKE) -S . -B $(BUILD_DIR) -DPSO_ENABLE_MPI=ON -DPSO_ENABLE_OPENMP=ON

# 注意：MinGW Makefiles 等单配置生成器忽略 --config；CMAKE_BUILD_TYPE 未设时
# 产出未优化构建。正式优化构建请直接走 CMake 并显式设 -DCMAKE_BUILD_TYPE=Release。
build: configure
	$(CMAKE) --build $(BUILD_DIR) --config Release --parallel

test: build
	ctest --test-dir $(BUILD_DIR) --output-on-failure -C Release

clean:
	$(CMAKE) -E rm -rf $(BUILD_DIR)
