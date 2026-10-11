# Builds the flight protocol code on a PC and generates cross-check vectors.
#   make -C tools/host_test          -> builds ./host_test and runs it into ./vectors
FLIGHT := ../../spacecraft/vgq1_flight/sketch/src
# Every pure-C++ (Arduino-free) flight source, plus every test_*.cpp in this folder:
# all of src/ccsds, src/drivers/radio.cpp (tables, LoRa maths, SX126x duty-cycle periods)
# and src/fsw except the two files that need the Arduino core (flight.cpp,
# status_display.cpp). drivers/sx126x.cpp is RadioLib-based (contract 25, which replaced
# the in-house sx126x_core layer of contract 23 rule 1): it is checked by
# check_firmware.sh and the real builds only.
FSW_HOST := $(filter-out $(FLIGHT)/fsw/flight.cpp $(FLIGHT)/fsw/status_display.cpp, \
              $(wildcard $(FLIGHT)/fsw/*.cpp))
SRCS   := host_test.cpp $(wildcard test_*.cpp) $(wildcard $(FLIGHT)/ccsds/*.cpp) \
          $(FSW_HOST) $(FLIGHT)/drivers/radio.cpp
# Every header the sources include, config.h first (tm.h, uslp.h, radio.h, ... include it):
# a header edit must rebuild the binary whose vectors the ground cross-checks use.
HDRS   := host_test.h $(FLIGHT)/config.h $(wildcard $(FLIGHT)/ccsds/*.h) $(wildcard $(FLIGHT)/fsw/*.h) \
          $(wildcard $(FLIGHT)/drivers/*.h)
CXX    ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wno-unused-parameter -DVGQ_HOST_BUILD -I$(FLIGHT)
# std::thread (rpc_mailbox test); kept out of CXXFLAGS so a CXXFLAGS override keeps it.
LDLIBS ?= -pthread

all: run

host_test: $(SRCS) $(HDRS)
	$(CXX) $(CXXFLAGS) -o $@ $(SRCS) $(LDLIBS)

run: host_test
	mkdir -p vectors
	./host_test vectors

clean:
	rm -rf host_test vectors

.PHONY: all run clean
