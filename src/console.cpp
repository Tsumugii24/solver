//
// Created by bytedance on 9.6.21.
//
#include "tools/CommandLineTool.h"
#include "tools/argparse.hpp"

#ifdef __linux__
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <execinfo.h>
#include <fcntl.h>
#include <sys/syscall.h>
#include <unistd.h>

namespace {
int g_stack_dump_fd = STDERR_FILENO;

void debugStackDumpHandler(int signum) {
    void* frames[128];
    const int frame_count = backtrace(frames, 128);
    const pid_t pid = getpid();
    const pid_t tid = static_cast<pid_t>(syscall(SYS_gettid));

    char header[256];
    const int header_len = std::snprintf(
            header,
            sizeof(header),
            "\n=== signal stack dump pid=%d tid=%d signal=%d ===\n",
            static_cast<int>(pid),
            static_cast<int>(tid),
            signum
    );
    if (header_len > 0) {
        write(g_stack_dump_fd, header, static_cast<size_t>(header_len));
    }
    backtrace_symbols_fd(frames, frame_count, g_stack_dump_fd);
    static const char trailer[] = "=== end signal stack dump ===\n";
    write(g_stack_dump_fd, trailer, sizeof(trailer) - 1);
}

void installDebugSignalHandlers() {
    const char* dump_path = std::getenv("POKER_SIGNAL_STACK_DUMP_FILE");
    if (dump_path != nullptr && dump_path[0] != '\0') {
        const int fd = open(dump_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (fd >= 0) {
            g_stack_dump_fd = fd;
        }
    }

    struct sigaction action {};
    std::memset(&action, 0, sizeof(action));
    action.sa_handler = debugStackDumpHandler;
    sigemptyset(&action.sa_mask);
    action.sa_flags = SA_RESTART;
    sigaction(SIGUSR1, &action, nullptr);
}
}
#endif

int main(int argc,const char **argv) {
#ifdef __linux__
    installDebugSignalHandlers();
#endif
    ArgumentParser parser;

    parser.addArgument("-i", "--input_file", 1, true);
    parser.addArgument("-r", "--resource_dir", 1, true);
    parser.addArgument("-m", "--mode", 1, true);

    parser.parse(argc, argv);

    string input_file = parser.retrieve<string>("input_file");
    string resource_dir = parser.retrieve<string>("resource_dir");
    if(resource_dir.empty()){
        resource_dir = "./resources";
    }
    string mode = parser.retrieve<string>("mode");
    if(mode.empty()){mode = "holdem";}
    if(mode != "holdem" && mode != "shortdeck")
        throw runtime_error(fmt::format("mode {} error, not in ['holdem','shortdeck']",mode));

    if(input_file.empty()) {
        CommandLineTool clt = CommandLineTool(mode,resource_dir);
        clt.startWorking();
    }else{
        cout << "EXEC FROM FILE" << endl;
        CommandLineTool clt = CommandLineTool(mode,resource_dir);
        clt.execFromFile(input_file);
    }
}
