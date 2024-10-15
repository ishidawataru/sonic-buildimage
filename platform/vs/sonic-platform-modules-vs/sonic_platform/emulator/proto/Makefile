PROTOC ?= python -m grpc_tools.protoc

# build Python gRPC modules
all: emulator.proto
	$(PROTOC) -I. --python_out=../ --grpc_python_out=../ emulator.proto
