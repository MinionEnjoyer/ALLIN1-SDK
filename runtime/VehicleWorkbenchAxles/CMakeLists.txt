cmake_minimum_required(VERSION 3.20)

project(VehicleWorkbenchAxles VERSION 4.4.0 LANGUAGES CXX)

# Story hosts are distributed as self-contained native plug-ins.  Keep the
# runtime library choice explicit so a developer machine's CMake defaults do
# not silently introduce a VC redistributable dependency.
if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

if(DEFINED VWA_BUILD_ASI_SKELETONS AND NOT DEFINED VWA_BUILD_STORY_HOSTS)
    set(VWA_BUILD_STORY_HOSTS "${VWA_BUILD_ASI_SKELETONS}" CACHE BOOL
        "Build the non-deployable Story Mode native host targets")
else()
    option(VWA_BUILD_STORY_HOSTS
        "Build the non-deployable Story Mode native host targets" ON)
endif()
# Compatibility alias for existing CI commands and downstream build scripts.
set(VWA_BUILD_ASI_SKELETONS "${VWA_BUILD_STORY_HOSTS}" CACHE BOOL
    "Deprecated alias for VWA_BUILD_STORY_HOSTS" FORCE)
option(VWA_BUILD_TESTS "Build platform-neutral runtime tests" ON)
option(VWA_BUILD_CONFIG_VALIDATOR
    "Build the native axle configuration validation helper" ON)

add_library(VehicleWorkbenchAxlesCore STATIC
    src/json.cpp
    src/configuration.cpp
    src/runtime.cpp
    src/wheel_access_adapters.cpp
)
target_include_directories(VehicleWorkbenchAxlesCore PUBLIC include)
target_compile_features(VehicleWorkbenchAxlesCore PUBLIC cxx_std_17)
target_compile_definitions(VehicleWorkbenchAxlesCore PUBLIC
    VWA_RUNTIME_VERSION="${PROJECT_VERSION}"
)
set_target_properties(VehicleWorkbenchAxlesCore PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)

if(MSVC)
    target_compile_options(VehicleWorkbenchAxlesCore PRIVATE /W4 /permissive-)
else()
    target_compile_options(VehicleWorkbenchAxlesCore PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(WIN32 AND VWA_BUILD_STORY_HOSTS)
    function(vwa_add_story_host target edition edition_define)
        add_library(${target} MODULE
            src/asi_entry.cpp
            src/story_host_bridge.cpp
        )
        target_link_libraries(${target} PRIVATE VehicleWorkbenchAxlesCore)
        target_link_libraries(${target} PRIVATE bcrypt version)
        target_include_directories(${target} PRIVATE src)
        target_compile_features(${target} PRIVATE cxx_std_17)
        target_compile_definitions(${target} PRIVATE
            ${edition_define}=1
            WIN32_LEAN_AND_MEAN
            NOMINMAX
        )
        if(MSVC)
            target_compile_options(${target} PRIVATE /W4 /permissive-)
        else()
            target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
            target_link_options(${target} PRIVATE -static-libgcc -static-libstdc++)
        endif()
        set_target_properties(${target} PROPERTIES
            PREFIX ""
            OUTPUT_NAME "VehicleWorkbenchAxles"
            SUFFIX ".asi"
            # Both edition targets intentionally share the deployed filename,
            # but MSVC also emits an import library with that basename. Keep
            # every link artifact edition-local so parallel builds cannot race
            # over a shared Release/VehicleWorkbenchAxles.{lib,exp} pair.
            ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/story-${edition}"
            RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/story-${edition}"
            LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/story-${edition}"
        )
    endfunction()
    vwa_add_story_host(
        VehicleWorkbenchAxlesLegacy Legacy VWA_ASI_EDITION_Legacy
    )
    vwa_add_story_host(
        VehicleWorkbenchAxlesEnhanced Enhanced VWA_ASI_EDITION_Enhanced
    )
endif()

if(VWA_BUILD_TESTS)
    enable_testing()
    add_executable(VehicleWorkbenchAxlesCoreTests tests/core_tests.cpp)
    target_link_libraries(VehicleWorkbenchAxlesCoreTests PRIVATE VehicleWorkbenchAxlesCore)
    target_compile_features(VehicleWorkbenchAxlesCoreTests PRIVATE cxx_std_17)
    add_test(NAME VehicleWorkbenchAxlesCoreTests COMMAND VehicleWorkbenchAxlesCoreTests)
endif()

if(VWA_BUILD_CONFIG_VALIDATOR)
    add_executable(VehicleWorkbenchAxlesConfigValidator
        tools/config_validator.cpp
    )
    target_link_libraries(VehicleWorkbenchAxlesConfigValidator
        PRIVATE VehicleWorkbenchAxlesCore
    )
    target_compile_features(VehicleWorkbenchAxlesConfigValidator PRIVATE cxx_std_17)
    if(MSVC)
        target_compile_options(VehicleWorkbenchAxlesConfigValidator
            PRIVATE /W4 /permissive-
        )
    else()
        target_compile_options(VehicleWorkbenchAxlesConfigValidator
            PRIVATE -Wall -Wextra -Wpedantic
        )
    endif()
endif()
