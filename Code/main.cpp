

#include <iostream>
#include "./Stitching/NISwGSP_Stitching.h"
#include "./Debugger/TimeCalculator.h"
#include "./Util/RuntimeConfig.h"

using namespace std;


int GRID_SIZE_w = 40;
int GRID_SIZE_h = 40;


namespace {
string getArgValue(int& index, int argc, const char* argv[], const string& arg) {
	const size_t eq_pos = arg.find('=');
	if (eq_pos != string::npos) {
		return arg.substr(eq_pos + 1);
	}
	if (index + 1 >= argc) {
		throw runtime_error("Missing value for " + arg);
	}
	return argv[++index];
}

vector<string> readDatasetsFile(const string& path) {
	ifstream file(path);
	if (!file.is_open()) {
		throw runtime_error("Failed to open datasets file: " + path);
	}

	vector<string> datasets;
	string line;
	while (getline(file, line)) {
		const size_t first = line.find_first_not_of(" \t\r\n");
		if (first == string::npos || line[first] == '#') {
			continue;
		}
		const size_t last = line.find_last_not_of(" \t\r\n");
		datasets.emplace_back(line.substr(first, last - first + 1));
	}
	return datasets;
}

void printUsage() {
	cout << "Usage: ges_gsp [options] [dataset ...]\n"
		<< "  --data-root PATH         Root containing dataset folders\n"
		<< "  --graph-root PATH        Root containing generated graph folders\n"
		<< "  --output-root PATH       Root for 0_results and 1_debugs\n"
		<< "  --method NAME            gsp or ges-gsp\n"
		<< "  --content-weight VALUE   Content preserving term weight\n"
		<< "  --max-target-megapixels VALUE  Abort if output canvas exceeds this size\n"
		<< "  --datasets-file PATH     Text file with one dataset name per line\n"
		<< "  --dataset NAME           Add one dataset name\n";
}
}

int main(int argc, const char* argv[]) {
	vector<string> data_list;

	try {
		for (int i = 1; i < argc; ++i) {
			string arg = argv[i];
			if (arg == "--help" || arg == "-h") {
				printUsage();
				return 0;
			}
			else if (arg.rfind("--data-root", 0) == 0) {
				g_runtime_config.data_root = normalizePath(getArgValue(i, argc, argv, arg));
			}
			else if (arg.rfind("--graph-root", 0) == 0) {
				g_runtime_config.graph_root = normalizePath(getArgValue(i, argc, argv, arg));
			}
			else if (arg.rfind("--output-root", 0) == 0) {
				g_runtime_config.output_root = normalizePath(getArgValue(i, argc, argv, arg));
			}
			else if (arg.rfind("--method", 0) == 0) {
				g_runtime_config.method = normalizeMethod(getArgValue(i, argc, argv, arg));
			}
			else if (arg.rfind("--content-weight", 0) == 0) {
				g_runtime_config.content_preserving_weight = stod(getArgValue(i, argc, argv, arg));
			}
			else if (arg.rfind("--max-target-megapixels", 0) == 0) {
				g_runtime_config.max_target_megapixels = stod(getArgValue(i, argc, argv, arg));
			}
			else if (arg.rfind("--datasets-file", 0) == 0) {
				vector<string> file_datasets = readDatasetsFile(getArgValue(i, argc, argv, arg));
				data_list.insert(data_list.end(), file_datasets.begin(), file_datasets.end());
			}
			else if (arg.rfind("--dataset", 0) == 0) {
				data_list.emplace_back(getArgValue(i, argc, argv, arg));
			}
			else if (arg.rfind("--", 0) == 0) {
				throw runtime_error("Unknown option: " + arg);
			}
			else {
				data_list.emplace_back(arg);
			}
		}
	}
	catch (const exception& ex) {
		cerr << ex.what() << endl;
		printUsage();
		return 2;
	}

	if (data_list.empty()) {
		data_list.emplace_back("CAVE-03_playroom-all");
	}
	if (!isKnownMethod()) {
		cerr << "Unknown method: " << g_runtime_config.method << endl;
		printUsage();
		return 2;
	}
	if (g_runtime_config.content_preserving_weight < 0) {
		cerr << "--content-weight must be non-negative." << endl;
		return 2;
	}
	if (g_runtime_config.max_target_megapixels <= 0) {
		cerr << "--max-target-megapixels must be positive." << endl;
		return 2;
	}

	Eigen::initParallel();
	CV_DNN_REGISTER_LAYER_CLASS(Crop, CropLayer);
	cout << "nThreads = " << Eigen::nbThreads() << endl;
	cout << "[#Images : " << data_list.size() << "]" << endl;
	cout << "data_root = " << g_runtime_config.data_root << endl;
	cout << "graph_root = " << g_runtime_config.graph_root << endl;
	cout << "output_root = " << g_runtime_config.output_root << endl;
	cout << "method = " << g_runtime_config.method << endl;
	cout << "result_suffix = " << resultSuffix() << endl;
	cout << "content_weight = " << g_runtime_config.content_preserving_weight << endl;
	cout << "max_target_megapixels = " << g_runtime_config.max_target_megapixels << endl;

	time_t start = clock();
	TimeCalculator timer;
	int failed_count = 0;
	for (int i = 0; i < data_list.size(); ++i) {
		cout << "i = " << i + 1 << ", [Images : " << data_list[i] << "]" << endl;
		try {
			MultiImages multi_images(data_list[i], LINES_FILTER_WIDTH, LINES_FILTER_LENGTH);

			NISwGSP_Stitching niswgsp(multi_images);
			niswgsp.setWeightToAlignmentTerm(1);
			niswgsp.setWeightToLocalSimilarityTerm(0.75);
			niswgsp.setWeightToGlobalSimilarityTerm(6, 20, GLOBAL_ROTATION_2D_METHOD);
			niswgsp.setWeightToContentPreservingTerm(g_runtime_config.content_preserving_weight);
			Mat blend_linear;
			vector<vector<Point2> > original_vertices;
			if (usesContentPreservingTerm()) {
				blend_linear = niswgsp.solve_content(BLEND_LINEAR, original_vertices);
			}
			else {
				blend_linear = niswgsp.solve(BLEND_LINEAR, original_vertices);
			}
			time_t end = clock();
			cout << "Time:" << double(end - start) / CLOCKS_PER_SEC << endl;
			niswgsp.writeImage(blend_linear, BLENDING_METHODS_NAME[BLEND_LINEAR]);

			niswgsp.assessment(original_vertices);
		}
		catch (const exception& ex) {
			++failed_count;
			cerr << "Failed dataset " << data_list[i] << ": " << ex.what() << endl;
		}
	}

	if (failed_count > 0) {
		return 1;
	}

	return 0;
}
