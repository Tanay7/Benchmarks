# Builds the flight protocol code on a PC and generates cross-check vectors.
#   make -C tools/host_test          -> builds ./host_test and runs it into ./vectors
FLIGHT := ../../spacecraft/vgq1_flight/sketch/src
# Every pure-C++ (Arduino-free) flight source, plus every test_*.cpp in this folder.
SRCS   := host_test.cpp $(wildcard test_*.cpp) $(wildcard $(FLIGHT)/ccsds/*.cpp) \
          $(FLIGHT)/fsw/tlm_packets.cpp $(FLIGHT)/fsw/commands.cpp \
          $(FLIGHT)/fsw/ssr.cpp $(FLIGHT)/fsw/attitude.cpp \
          $(wildcard $(FLIGHT)/core/*.cpp)
CXX    ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wno-unused-parameter -DVGQ_HOST_BUILD -I$(FLIGHT)

all: run

host_test: $(SRCS) host_test.h $(wildcard $(FLIGHT)/ccsds/*.h) $(wildcard $(FLIGHT)/fsw/*.h) $(wildcard $(FLIGHT)/core/*.h)
	$(CXX) $(CXXFLAGS) -o $@ $(SRCS)

run: host_test
	mkdir -p vectors
	./host_test vectors

clean:
	rm -rf host_test vectors

.PHONY: all run clean
