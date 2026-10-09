# Builds the flight protocol code on a PC and generates cross-check vectors.
#   make -C tools/host_test          -> builds ./host_test and runs it into ./vectors
FLIGHT := ../../spacecraft/vgq1_flight/sketch/src
SRCS   := host_test.cpp $(FLIGHT)/ccsds/crc16.cpp $(FLIGHT)/ccsds/randomizer.cpp \
          $(FLIGHT)/ccsds/reed_solomon.cpp $(FLIGHT)/ccsds/tm.cpp $(FLIGHT)/ccsds/tc.cpp \
          $(FLIGHT)/fsw/tlm_packets.cpp $(FLIGHT)/fsw/commands.cpp \
          $(FLIGHT)/fsw/ssr.cpp $(FLIGHT)/fsw/attitude.cpp
CXX    ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wno-unused-parameter -DVGQ_HOST_BUILD

all: run

host_test: $(SRCS) $(wildcard $(FLIGHT)/ccsds/*.h) $(wildcard $(FLIGHT)/fsw/*.h)
	$(CXX) $(CXXFLAGS) -o $@ $(SRCS)

run: host_test
	mkdir -p vectors
	./host_test vectors

clean:
	rm -rf host_test vectors

.PHONY: all run clean
