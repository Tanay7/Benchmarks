# Builds the flight protocol code on a PC and generates cross-check vectors.
#   make -C tools/host_test          -> builds ./host_test and runs it into ./vectors
FLIGHT := ../../spacecraft/vgq1_flight/sketch/src
# Every pure-C++ (Arduino-free) flight source, plus every test_*.cpp in this folder:
# all of src/ccsds, src/drivers/radio.cpp (tables + LoRa maths), src/drivers/sx126x_core.cpp
# (SX126x command layer) and src/fsw except the two files that need the Arduino core
# (flight.cpp, status_display.cpp).
FSW_HOST := $(filter-out $(FLIGHT)/fsw/flight.cpp $(FLIGHT)/fsw/status_display.cpp, \
              $(wildcard $(FLIGHT)/fsw/*.cpp))
SRCS   := host_test.cpp $(wildcard test_*.cpp) $(wildcard $(FLIGHT)/ccsds/*.cpp) \
          $(FSW_HOST) $(wildcard $(FLIGHT)/drivers/radio.cpp $(FLIGHT)/drivers/sx126x_core.cpp)
CXX    ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wno-unused-parameter -DVGQ_HOST_BUILD -I$(FLIGHT)

all: run

host_test: $(SRCS) host_test.h $(wildcard $(FLIGHT)/ccsds/*.h) $(wildcard $(FLIGHT)/fsw/*.h) $(wildcard $(FLIGHT)/drivers/radio.h $(FLIGHT)/drivers/sx126x_core.h)
	$(CXX) $(CXXFLAGS) -o $@ $(SRCS)

run: host_test
	mkdir -p vectors
	./host_test vectors

clean:
	rm -rf host_test vectors

.PHONY: all run clean
