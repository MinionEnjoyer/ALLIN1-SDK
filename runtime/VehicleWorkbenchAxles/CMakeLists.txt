cmake_minimum_required(VERSION 3.20)

project(VehicleWorkbenchAxles VERSION 2.1.0 LANGUAGES CXX)

option(VWA_BUILD_ASI_SKELETONS "Build the non-deployable Story Mode ASI skeleton targets" ON)
option(VWA_BUILD_TESTS "Build platform-neutral runtime tests" ON)

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

if(WIN32 AND VWA_BUILD_ASI_SKELETONS)
    function(vwa_add_asi_skeleton target edition edition_define)
        add_library(${target} MODULE src/asi_entry.cpp)
        target_link_libraries(${target} PRIVATE VehicleWorkbenchAxlesCore)
        target_compile_features(${target} PRIVATE cxx_std_17)
        target_compile_definitions(${target} PRIVATE
            ${edition_define}=1
        )
        set_target_properties(${target} PROPERTIES
            PREFIX ""
            OUTPUT_NAME "VehicleWorkbenchAxles"
            SUFFIX ".asi"
            RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/story-${edition}"
            LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/story-${edition}"
        )
    endfunction()
    vwa_add_asi_skeleton(
        VehicleWorkbenchAxlesLegacy Legacy VWA_ASI_EDITION_Legacy
    )
    vwa_add_asi_skeleton(
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
