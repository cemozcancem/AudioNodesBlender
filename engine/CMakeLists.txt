cmake_minimum_required(VERSION 3.22)
project(AudioNodesEngine VERSION 0.0.1 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Fetch JUCE
include(FetchContent)

FetchContent_Declare(
    JUCE
    GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
    GIT_TAG 8.0.4
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(JUCE)

# ---------------------------------------------------------------------------
# ZeroMQ
#
# Unix keeps pkg-config (Homebrew/apt ship a libzmq.pc). Windows has no
# pkg-config, so `find_package(PkgConfig REQUIRED)` used to fail configure
# outright - the first and hardest Windows blocker. There we use the CMake
# package config vcpkg installs:
#
#     vcpkg install zeromq:x64-windows-static-md
#     cmake -B build -S . -DCMAKE_TOOLCHAIN_FILE=<vcpkg>/scripts/buildsystems/vcpkg.cmake \
#                         -DVCPKG_TARGET_TRIPLET=x64-windows-static-md
#
# Both branches end up defining ZMQ_LINK_TARGETS, which the link step below
# consumes, so nothing downstream has to care which one ran.
# ---------------------------------------------------------------------------
if(WIN32)
    find_package(ZeroMQ CONFIG REQUIRED)

    # vcpkg exports `libzmq` for a shared build and `libzmq-static` for a
    # static one; which exists depends on the triplet. Prefer static: it
    # leaves nothing to copy next to the .exe.
    if(TARGET libzmq-static)
        set(ZMQ_LINK_TARGETS libzmq-static)
        set(ZMQ_IS_STATIC TRUE)
    elseif(TARGET libzmq)
        set(ZMQ_LINK_TARGETS libzmq)
        set(ZMQ_IS_STATIC FALSE)
    else()
        message(FATAL_ERROR
            "ZeroMQ was found but exported neither libzmq nor libzmq-static. "
            "Install it with: vcpkg install zeromq:x64-windows-static-md")
    endif()
    message(STATUS "ZeroMQ: linking ${ZMQ_LINK_TARGETS} (static=${ZMQ_IS_STATIC})")
else()
    find_package(PkgConfig REQUIRED)
    pkg_check_modules(ZMQ REQUIRED libzmq)
    set(ZMQ_LINK_TARGETS ${ZMQ_LIBRARIES})
endif()

# Fetch cppzmq (header-only wrapper)
FetchContent_Declare(
    cppzmq
    GIT_REPOSITORY https://github.com/zeromq/cppzmq.git
    GIT_TAG v4.10.0
    GIT_SHALLOW TRUE
)
set(CPPZMQ_BUILD_TESTS OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(cppzmq)

# Fetch nlohmann/json for JSON parsing
FetchContent_Declare(
    json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG v3.11.3
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(json)

# AudioNodesEngine executable
juce_add_gui_app(AudioNodesEngine
    PRODUCT_NAME "Audio Nodes Engine"
    BUNDLE_ID "com.audionodes.engine"
    COMPANY_NAME "AudioNodes"
    VERSION "0.0.1"
    NEEDS_CURL FALSE
    NEEDS_WEB_BROWSER FALSE
)

# Source files
target_sources(AudioNodesEngine PRIVATE
    src/Main.cpp
    src/AudioEngine.cpp
    src/NodeManager.cpp
    src/CommandServer.cpp
    src/SharedMemoryBridge.cpp
    src/MIDIManager.cpp
    src/nodes/BaseNode.cpp
    src/nodes/InputNode.cpp
    src/nodes/OutputNode.cpp
    src/nodes/OscillatorNode.cpp
    src/nodes/FilterNode.cpp
    src/nodes/GainNode.cpp
    src/nodes/DelayNode.cpp
    src/nodes/FFTAnalyzerNode.cpp
    src/nodes/MIDIInputNode.cpp
    src/nodes/MIDIOutputNode.cpp
    src/nodes/SequencerNode.cpp
    src/nodes/BangNode.cpp
    src/nodes/SamplerNode.cpp
    src/nodes/DrumSamplerNode.cpp
    src/nodes/ADSRNode.cpp
    src/nodes/MixerNode.cpp
    src/nodes/MathNode.cpp
)

# macOS Objective-C++ sources
if(APPLE)
    target_sources(AudioNodesEngine PRIVATE
        src/MacAudioPermissions.mm
    )
endif()

# Include directories
target_include_directories(AudioNodesEngine PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${CMAKE_CURRENT_SOURCE_DIR}/src/nodes
    ${CMAKE_CURRENT_SOURCE_DIR}/src/common
    ${cppzmq_SOURCE_DIR}
)

# ZeroMQ include/lib paths (pkg-config only - the vcpkg imported target
# already carries its own usage requirements).
if(NOT WIN32)
    target_include_directories(AudioNodesEngine PRIVATE ${ZMQ_INCLUDE_DIRS})
    target_link_directories(AudioNodesEngine PRIVATE ${ZMQ_LIBRARY_DIRS})
endif()

# JUCE modules and dependencies
target_link_libraries(AudioNodesEngine PRIVATE
    juce::juce_audio_basics
    juce::juce_audio_devices
    juce::juce_audio_formats
    juce::juce_audio_processors
    juce::juce_audio_utils
    juce::juce_core
    juce::juce_dsp
    juce::juce_events
    juce::juce_graphics
    juce::juce_gui_basics
    ${ZMQ_LINK_TARGETS}
    nlohmann_json::nlohmann_json
)

# JUCE compile definitions
target_compile_definitions(AudioNodesEngine PRIVATE
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0
    JUCE_APPLICATION_NAME_STRING="$<TARGET_PROPERTY:AudioNodesEngine,JUCE_PRODUCT_NAME>"
    JUCE_APPLICATION_VERSION_STRING="$<TARGET_PROPERTY:AudioNodesEngine,JUCE_VERSION>"
    JUCE_DISPLAY_SPLASH_SCREEN=0
    JUCE_REPORT_APP_USAGE=0
    JUCE_STRICT_REFCOUNTEDPOINTER=1
)

# Platform-specific settings
if(APPLE)
    # macOS Silicon support
    set(CMAKE_OSX_ARCHITECTURES "arm64" CACHE STRING "Build for Apple Silicon")
    set(CMAKE_OSX_DEPLOYMENT_TARGET "12.0" CACHE STRING "Minimum macOS version")
    
    # Request microphone permission
    set_target_properties(AudioNodesEngine PROPERTIES
        MACOSX_BUNDLE TRUE
        MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_SOURCE_DIR}/resources/Info.plist"
        MACOSX_BUNDLE_SHORT_VERSION_STRING "${PROJECT_VERSION}"
        MACOSX_BUNDLE_BUNDLE_VERSION "${PROJECT_VERSION}"
    )
    
    # Link AVFoundation for audio permissions
    target_link_libraries(AudioNodesEngine PRIVATE
        "-framework AVFoundation"
        "-framework CoreAudio"
        "-framework AudioToolbox"
    )
elseif(WIN32)
    # NOTE: there is deliberately no CMAKE_MSVC_RUNTIME_LIBRARY here.
    # It used to be set in this block, which is *after* juce_add_gui_app()
    # created the target - CMake reads that variable when a target is
    # created, so it did nothing at all. Setting it for real (static CRT)
    # would also clash with a vcpkg libzmq built against the dynamic CRT.
    # Let the vcpkg triplet decide: -md triplets = dynamic CRT, which is
    # what JUCE and the default MSVC settings expect.

    # A static libzmq needs ZMQ_STATIC before zmq.h, or every symbol is
    # declared __declspec(dllimport) and the link fails with unresolved
    # externals. vcpkg's imported target usually carries this already;
    # defining it twice is harmless, missing it is not.
    if(ZMQ_IS_STATIC)
        target_compile_definitions(AudioNodesEngine PRIVATE ZMQ_STATIC)
    endif()

    # windows.h's min/max macros collide with std::min/std::max - without
    # NOMINMAX, SharedMemoryBridge.cpp alone fails with 17 errors. Set for the
    # whole target so any future file that reaches for windows.h is safe.
    target_compile_definitions(AudioNodesEngine PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)

    # Console-free GUI subsystem app (JUCE already does this), and give the
    # engine a sane name in Task Manager.
    set_target_properties(AudioNodesEngine PROPERTIES
        OUTPUT_NAME "AudioNodesEngine"
        WIN32_EXECUTABLE TRUE
    )
endif()

# Copy binary to blender addon bin folder post-build
if(APPLE)
    set(OUTPUT_DIR "${CMAKE_SOURCE_DIR}/../blender_addon/bin/darwin_arm64")
    add_custom_command(TARGET AudioNodesEngine POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory "${OUTPUT_DIR}"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "$<TARGET_BUNDLE_DIR:AudioNodesEngine>"
            "${OUTPUT_DIR}/AudioNodesEngine.app"
        COMMENT "Copying AudioNodesEngine.app to blender_addon/bin/darwin_arm64/"
    )
elseif(WIN32)
    set(OUTPUT_DIR "${CMAKE_SOURCE_DIR}/../blender_addon/bin/win_x64")
    add_custom_command(TARGET AudioNodesEngine POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory "${OUTPUT_DIR}"
        COMMAND ${CMAKE_COMMAND} -E copy
            "$<TARGET_FILE:AudioNodesEngine>"
            "${OUTPUT_DIR}/AudioNodesEngine.exe"
        COMMENT "Copying AudioNodesEngine.exe to blender_addon/bin/win_x64/"
    )

    # With a SHARED libzmq the .exe alone is not runnable - libzmq's DLL
    # (and libsodium's, if it pulled one in) must sit beside it or the
    # engine dies at load time with no message the add-on can surface.
    # A static triplet leaves this list empty and the command is skipped.
    add_custom_command(TARGET AudioNodesEngine POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E $<IF:$<BOOL:$<TARGET_RUNTIME_DLLS:AudioNodesEngine>>,copy_if_different,true>
            "$<TARGET_RUNTIME_DLLS:AudioNodesEngine>" "${OUTPUT_DIR}"
        COMMAND_EXPAND_LISTS
        COMMENT "Copying runtime DLLs (none if ZeroMQ was linked statically)"
    )
endif()

