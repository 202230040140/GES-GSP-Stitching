#pragma once

#include <string>

struct RuntimeConfig {
	std::string data_root = "./input-data";
	std::string graph_root = "";
	std::string output_root = "./input-data";
	std::string method = "ges-gsp";
	double content_preserving_weight = 1.5;
	double max_target_megapixels = 80.0;
};

extern RuntimeConfig g_runtime_config;

std::string normalizeMethod(std::string method);
std::string normalizePath(std::string path);
std::string joinPath(const std::string& left, const std::string& right);
void ensureDirectory(const std::string& path);
bool isKnownMethod();
bool usesContentPreservingTerm();
std::string resultSuffix();
