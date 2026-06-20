#include "RuntimeConfig.h"

#include <algorithm>
#include <cctype>
#include <filesystem>

RuntimeConfig g_runtime_config;

std::string normalizeMethod(std::string method) {
	std::transform(method.begin(), method.end(), method.begin(), [](unsigned char ch) {
		return (char)std::tolower(ch);
	});
	if (method == "ours" || method == "ges" || method == "ges-gsp") {
		return "ges-gsp";
	}
	return method;
}

std::string normalizePath(std::string path) {
	for (char& ch : path) {
		if (ch == '\\') {
			ch = '/';
		}
	}
	while (path.size() > 1 && path.back() == '/') {
		path.pop_back();
	}
	return path;
}

std::string joinPath(const std::string& left, const std::string& right) {
	if (left.empty()) {
		return normalizePath(right);
	}
	if (right.empty()) {
		return normalizePath(left);
	}

	std::string lhs = normalizePath(left);
	std::string rhs = normalizePath(right);
	if (!rhs.empty() && (rhs[0] == '/' || (rhs.size() > 1 && rhs[1] == ':'))) {
		return rhs;
	}
	return lhs + "/" + rhs;
}

void ensureDirectory(const std::string& path) {
	if (!path.empty()) {
		std::filesystem::create_directories(std::filesystem::path(path));
	}
}

bool isKnownMethod() {
	const std::string method = normalizeMethod(g_runtime_config.method);
	return method == "gsp" || method == "ges-gsp";
}

bool usesContentPreservingTerm() {
	return normalizeMethod(g_runtime_config.method) == "ges-gsp";
}

std::string resultSuffix() {
	const std::string method = normalizeMethod(g_runtime_config.method);
	if (method == "gsp") {
		return "GSP_";
	}
	return "GES-GSP_";
}
