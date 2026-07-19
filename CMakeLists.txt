cmake_minimum_required(VERSION 3.25)

set(mpmt "Multiparty-Private-Membership-Test")
project(${mpmt} VERSION 1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# User-local install prefix (emp-sh2pc)
list(APPEND CMAKE_PREFIX_PATH "$ENV{HOME}/.local")

# ================ 1. common dependencies (all build modes) ================
find_package(emp-tool CONFIG REQUIRED)
find_package(emp-sh2pc CONFIG REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(SODIUM REQUIRED libsodium)

# ================ 2. compile cfg ================
add_compile_options(-march=native)

if(CMAKE_BUILD_TYPE STREQUAL "Debug" OR NOT CMAKE_BUILD_TYPE)
    add_compile_options(-g -O0)
    message(STATUS "Building in Debug mode.")
elseif(CMAKE_BUILD_TYPE STREQUAL "Release")
    add_compile_options(-O3)
    message(STATUS "Building in Release mode.")
endif()

# ================ 3. Python bindings (scikit-build only) ================
if(SKBUILD)

find_package(Python COMPONENTS Interpreter Development REQUIRED)

find_package(nanobind REQUIRED)

nanobind_add_module(_mpmt NB_DOMAIN mpmt NOMINSIZE
    src/bind/rvector_bind.cpp
    src/bind/util_bind.cpp
    src/bind/dpf_bind.cpp
    src/bind/aby3_bind.cpp
    src/bind/emp2_bind.cpp
)

target_include_directories(_mpmt
    PRIVATE ${PROJECT_SOURCE_DIR}/include
    PRIVATE ${PROJECT_SOURCE_DIR}/src/bind
    PRIVATE ${SODIUM_INCLUDE_DIRS}
)

target_link_libraries(_mpmt
    PRIVATE
    ${SODIUM_LIBRARIES}
    emp-tool::emp-tool
    emp-sh2pc::emp-sh2pc
    nlohmann_json::nlohmann_json
)

install(TARGETS _mpmt LIBRARY DESTINATION mpmt)

endif()  # SKBUILD (Python bindings)
