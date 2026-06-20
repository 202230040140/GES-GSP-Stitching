cmake_minimum_required(VERSION 3.18)

project(GES_GSP LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT DEFINED CONDA_PREFIX)
  set(CONDA_PREFIX "$ENV{CONDA_PREFIX}")
endif()

if(NOT CONDA_PREFIX)
  message(FATAL_ERROR "Set CONDA_PREFIX to the obj-gsp-cpp environment path.")
endif()

set(OpenCV_DIR "${CONDA_PREFIX}/Library/cmake/x64/vc15/lib")
find_package(OpenCV REQUIRED)
find_package(OpenMP)

add_executable(ges_gsp
  main.cpp
  Debugger/ColorMap.cpp
  Debugger/ErrorController.cpp
  Debugger/ImageDebugger.cpp
  Debugger/TimeCalculator.cpp
  Feature/FeatureController.cpp
  Feature/ImageData.cpp
  Feature/MultiImage.cpp
  Mesh/MeshGrid.cpp
  Mesh/MeshOptimization.cpp
  Stitching/APAP_Stitching.cpp
  Stitching/NISwGSP_Stitching.cpp
  Stitching/Parameter.cpp
  Util/Blending.cpp
  Util/EdgeDetection.cpp
  Util/InputParser.cpp
  Util/RuntimeConfig.cpp
  Util/Statistics.cpp
  Util/Thin.cpp
  Util/Transform.cpp
  lsd.cpp
)

target_include_directories(ges_gsp PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}"
  "${CONDA_PREFIX}/Library/include"
  "${CONDA_PREFIX}/Library/include/eigen3"
)

target_link_directories(ges_gsp PRIVATE
  "${CONDA_PREFIX}/Library/lib"
)

target_link_libraries(ges_gsp PRIVATE
  ${OpenCV_LIBS}
  "${CONDA_PREFIX}/Library/lib/gflags.lib"
  "${CONDA_PREFIX}/Library/bin/vl.lib"
)

if(OpenMP_CXX_FOUND)
  target_link_libraries(ges_gsp PRIVATE OpenMP::OpenMP_CXX)
endif()

file(GLOB GES_GSP_RUNTIME_DLLS
  "${CONDA_PREFIX}/Library/bin/*.dll"
  "${CONDA_PREFIX}/*.dll"
)

add_custom_command(TARGET ges_gsp POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E copy_if_different
          ${GES_GSP_RUNTIME_DLLS}
          $<TARGET_FILE_DIR:ges_gsp>
  COMMAND ${CMAKE_COMMAND} -E copy_if_different
          "${CONDA_PREFIX}/Library/bin/deflate.dll"
          "$<TARGET_FILE_DIR:ges_gsp>/libdeflate.dll"
  COMMAND ${CMAKE_COMMAND} -E copy_directory
          "${CMAKE_CURRENT_SOURCE_DIR}/model"
          "$<TARGET_FILE_DIR:ges_gsp>/model"
)

target_compile_definitions(ges_gsp PRIVATE
  _CRT_SECURE_NO_WARNINGS
  NOMINMAX
  _USE_MATH_DEFINES
)
