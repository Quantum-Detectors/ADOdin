import os
from string import Template

from dls_dependency_tree import dependency_tree

from iocbuilder import AutoSubstitution, Device
from iocbuilder.arginfo import makeArgInfo, Simple, Ident
from iocbuilder.iocinit import IocDataStream
from iocbuilder.modules.asyn import AsynPort
from iocbuilder.modules.ADCore import ADCore, ADBaseTemplate, makeTemplateInstance
from iocbuilder.modules.restClient import restClient


ADODIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ADODIN_DATA = os.path.join(ADODIN_ROOT, "data")

TREE = None


def find_module_path(module):
    global TREE
    if TREE is None:
        TREE = dependency_tree()
        TREE.process_module(ADODIN_ROOT)
    for macro, path in TREE.macros.items():
        if "/{}/".format(module) in path:
            return macro, path


ODIN_DATA_MACRO, ODIN_DATA_ROOT = find_module_path("odin-data")
print("OdinData: {} = {}".format(ODIN_DATA_MACRO, ODIN_DATA_ROOT))


def expand_template_file(template, macros, output_file, executable=False):
    if executable:
        mode = 0755
    else:
        mode = None

    with open(os.path.join(ADODIN_DATA, template)) as template_file:
        template_config = Template(template_file.read())

    output = template_config.substitute(macros)
    print("--- {} ----------------------------------------------".format(output_file))
    print(output)
    print("---")

    stream = IocDataStream(output_file, mode)
    stream.write(output)


class _OdinDataTemplate(AutoSubstitution):
    TemplateFile = "odinData.template"


class _OdinData(Device):

    """Store configuration for an OdinData process"""
    INDEX = 1  # Unique index for each OdinData instance

    # Device attributes
    AutoInstantiate = True

    def __init__(self, IP, READY, RELEASE, META):
        self.__super.__init__()
        # Update attributes with parameters
        self.__dict__.update(locals())

        # Create unique R MACRO for template file - OD1, OD2 etc.
        self.R = ":OD{}:".format(self.INDEX)
        self.index = _OdinData.INDEX
        _OdinData.INDEX += 1

    def create_config_file(self, prefix, template, index, extra_macros=None):
        macros = dict(IP=self.IP, RD_PORT=self.READY, RL_PORT=self.RELEASE,
                      FW_ROOT=ODIN_DATA_ROOT)
        if extra_macros is not None:
            macros.update(extra_macros)

        expand_template_file(template, macros, "{}{}.json".format(prefix, index))

    def create_config_files(self, index):
        raise NotImplementedError("Method must be implemented by child classes")


class _OdinDataServer(Device):

    """Store configuration for an OdinDataServer"""
    ODIN_DATA_CLASS = _OdinData
    PORT_BASE = 5000
    PROCESS_COUNT = 0

    # Device attributes
    AutoInstantiate = True

    def __init__(self, IP, PROCESSES, SHARED_MEM_SIZE):
        self.__super.__init__()
        # Update attributes with parameters
        self.__dict__.update(locals())

        self.processes = []
        for _ in range(PROCESSES):
            self.processes.append(
                self.create_odin_data_process(
                    IP, self.PORT_BASE + 1, self.PORT_BASE + 2, self.PORT_BASE + 3)
            )
            self.PORT_BASE += 10

        self.instantiated = False  # Make sure instances are only used once

    ArgInfo = makeArgInfo(__init__,
        IP=Simple("IP address of server hosting OdinData processes", str),
        PROCESSES=Simple("Number of OdinData processes on this server", int),
        SHARED_MEM_SIZE=Simple("Size of shared memory buffers in bytes", int),
    )

    def create_odin_data_process(self, ip, ready, release, meta):
        raise NotImplementedError("Method must be implemented by child classes")

    def create_od_startup_scripts(self, server_rank, total_servers):
        suffix = server_rank
        for idx, _ in enumerate(self.processes):
            fp_port_number = 5004 + (10 * idx)
            fr_port_number = 5000 + (10 * idx)
            ready_port_number = 5001 + (10 * idx)
            release_port_number = 5002 + (10 * idx)

            output_file = "stFrameReceiver{}.sh".format(suffix)
            macros = dict(
                OD_ROOT=ODIN_DATA_ROOT,
                BUFFER_IDX=idx + 1, SHARED_MEMORY=self.SHARED_MEM_SIZE,
                CTRL_PORT=fr_port_number,
                READY_PORT=ready_port_number, RELEASE_PORT=release_port_number,
                LOG_CONFIG=os.path.join(ADODIN_DATA, "fr_log4cxx.xml"))
            expand_template_file("fr_startup", macros, output_file, executable=True)

            output_file = "stFrameProcessor{}.sh".format(suffix)
            macros = dict(
                OD_ROOT=ODIN_DATA_ROOT,
                CTRL_PORT=fp_port_number,
                READY_PORT=ready_port_number, RELEASE_PORT=release_port_number,
                LOG_CONFIG=os.path.join(ADODIN_DATA, "fp_log4cxx.xml"))
            expand_template_file("fp_startup", macros, output_file, executable=True)

            suffix += total_servers


class _OdinDetectorTemplate(AutoSubstitution):
    TemplateFile = "odinDetector.template"


class _OdinControlServer(Device):

    """Store configuration for an OdinControlServer"""

    ODIN_SERVER = None
    ADAPTERS = ["fp", "fr"]
    PORT = 8888

    # Device attributes
    AutoInstantiate = True

    def __init__(self, IP,
                 NODE_1_CTRL_IP, NODE_2_CTRL_IP, NODE_3_CTRL_IP, NODE_4_CTRL_IP,
                 NODE_5_CTRL_IP, NODE_6_CTRL_IP, NODE_7_CTRL_IP, NODE_8_CTRL_IP):
        self.__super.__init__()
        # Update attributes with parameters
        self.__dict__.update(locals())

        self.ips = [
            self.NODE_1_CTRL_IP, self.NODE_2_CTRL_IP, self.NODE_3_CTRL_IP, self.NODE_4_CTRL_IP,
            self.NODE_5_CTRL_IP, self.NODE_6_CTRL_IP, self.NODE_7_CTRL_IP, self.NODE_8_CTRL_IP
        ]
        if all(_ is None for _ in self.ips):
            raise ValueError("Received no control endpoints for Odin Server")

        self.create_odin_server_startup_scripts()
        self.create_odin_server_config_file()

    ArgInfo = makeArgInfo(__init__,
        IP=Simple("IP address of control server", str),
        NODE_1_CTRL_IP=Simple("IP address for control of FR and FP", str),
        NODE_2_CTRL_IP=Simple("IP address for control of FR and FP", str),
        NODE_3_CTRL_IP=Simple("IP address for control of FR and FP", str),
        NODE_4_CTRL_IP=Simple("IP address for control of FR and FP", str),
        NODE_5_CTRL_IP=Simple("IP address for control of FR and FP", str),
        NODE_6_CTRL_IP=Simple("IP address for control of FR and FP", str),
        NODE_7_CTRL_IP=Simple("IP address for control of FR and FP", str),
        NODE_8_CTRL_IP=Simple("IP address for control of FR and FP", str)
    )

    def create_odin_server_startup_scripts(self):
        macros = dict(ODIN_SERVER=self.ODIN_SERVER, CONFIG="odin_server.cfg")
        expand_template_file("odin_server_startup", macros, "stOdinServer.sh", executable=True)

    def create_odin_server_config_file(self):
        macros = dict(PORT=self.PORT,
                      ADAPTERS=", ".join(self.ADAPTERS),
                      ADAPTER_CONFIG="\n\n".join(self.create_odin_server_config_entries()))
        expand_template_file("odin_server.ini", macros, "odin_server.cfg")

    def create_odin_server_config_entries(self):
        raise NotImplementedError("Method must be implemented by child classes")

    def _create_odin_data_config_entry(self):
        fp_endpoints = []
        fr_endpoints = []
        server_count = {}
        for ip in self.ips:
            if ip is not None:
                if ip not in server_count:
                    server_count[ip] = 0
                fp_port_number = 5004 + (10 * server_count[ip])
                fr_port_number = 5000 + (10 * server_count[ip])
                server_count[ip] += 1
                fr_endpoints.append("{}:{}".format(ip, fr_port_number))
                fp_endpoints.append("{}:{}".format(ip, fp_port_number))

        return "[adapter.fp]\n" \
               "module = odin_data.frame_processor_adapter.FrameProcessorAdapter\n" \
               "endpoints = {}\n" \
               "update_interval = 0.2\n\n" \
               "[adapter.fr]\n" \
               "module = odin_data.odin_data_adapter.OdinDataAdapter\n" \
               "endpoints = {}\n" \
               "update_interval = 0.2".format(", ".join(fp_endpoints), ", ".join(fr_endpoints))


class _OdinDetector(AsynPort):

    """Create an odin detector"""

    Dependencies = (ADCore, restClient)

    # This tells xmlbuilder to use PORT instead of name as the row ID
    UniqueName = "PORT"

    def __init__(self, PORT, ODIN_CONTROL_SERVER, DETECTOR, BUFFERS = 0, MEMORY = 0, **args):
        # Init the superclass (AsynPort)
        self.__super.__init__(PORT)
        # Update the attributes of self from the commandline args
        self.__dict__.update(locals())

        self.CONTROL_SERVER_IP = ODIN_CONTROL_SERVER.IP
        self.CONTROL_SERVER_PORT = ODIN_CONTROL_SERVER.PORT

    # __init__ arguments
    ArgInfo = ADBaseTemplate.ArgInfo + makeArgInfo(__init__,
        PORT=Simple("Port name for the detector", str),
        ODIN_CONTROL_SERVER=Ident("Odin control server", _OdinControlServer),
        DETECTOR=Simple("Name of detector", str),
        BUFFERS=Simple("Maximum number of NDArray buffers to be created for plugin callbacks", int),
        MEMORY=Simple("Max memory to allocate, should be maxw*maxh*nbuffer for driver and all "
                      "attached plugins", int)
    )

    # Device attributes
    LibFileList = ['odinDetector']
    DbdFileList = ['odinDetectorSupport']

    def Initialise(self):
        print "# odinDetectorConfig(const char * portName, const char * serverPort, " \
              "int odinServerPort, const char * detectorName, " \
              "int maxBuffers, size_t maxMemory, int priority, int stackSize)"
        print "odinDetectorConfig(\"%(PORT)s\", \"%(CONTROL_SERVER_IP)s\", " \
              "%(CONTROL_SERVER_PORT)d, \"%(DETECTOR)s\", " \
              "%(BUFFERS)d, %(MEMORY)d)" % self.__dict__


class _OdinDataDriverTemplate(AutoSubstitution):
    TemplateFile = "odinDataDriver.template"


class _OdinDataDriver(AsynPort):

    """Create an OdinData driver"""

    Dependencies = (ADCore, restClient)

    # This tells xmlbuilder to use PORT instead of name as the row ID
    UniqueName = "PORT"

    _SpecificTemplate = _OdinDataDriverTemplate

    def __init__(self, PORT, ODIN_CONTROL_SERVER, DETECTOR=None, DATASET="data",
                 ODIN_DATA_SERVER_1=None, ODIN_DATA_SERVER_2=None, ODIN_DATA_SERVER_3=None,
                 ODIN_DATA_SERVER_4=None, ODIN_DATA_SERVER_5=None, ODIN_DATA_SERVER_6=None,
                 ODIN_DATA_SERVER_7=None, ODIN_DATA_SERVER_8=None,
                 BUFFERS=0, MEMORY=0,
                 **args):
        # Init the superclass (AsynPort)
        self.__super.__init__(PORT)
        # Update the attributes of self from the commandline args
        self.__dict__.update(locals())
        # Make an instance of our template
        makeTemplateInstance(self._SpecificTemplate, locals(), args)

        self.CONTROL_SERVER_IP = ODIN_CONTROL_SERVER.IP
        self.CONTROL_SERVER_PORT = ODIN_CONTROL_SERVER.PORT
        self.DETECTOR_PLUGIN = DETECTOR.lower()
        self.ODIN_DATA_PROCESSES = []

        # Count the number of servers
        self.server_count = 0
        for idx in range(1, 9):
            server = eval("ODIN_DATA_SERVER_{}".format(idx))
            if server is not None:
                self.server_count += 1
        print("Server count: {}".format(self.server_count))

        server_number = 0
        for idx in range(1, 9):
            server = eval("ODIN_DATA_SERVER_{}".format(idx))
            if server is not None:
                if server.instantiated:
                    raise ValueError("Same OdinDataServer object given twice")
                else:
                    server_number += 1
                    server.instantiated = True

                index_number = 0
                for odin_data in server.processes:
                    address = idx + index_number - 1
                    print("Odin data idx: {}  index_number: {}  address: {}".format(idx, index_number, address))
                    self.ODIN_DATA_PROCESSES.append(odin_data)
                    # Use some OdinDataDriver macros to instantiate an odinData.template
                    args["PORT"] = PORT
                    args["ADDR"] = odin_data.index-1
                    args["R"] = odin_data.R
                    _OdinDataTemplate(**args)

                    odin_data.create_config_files(address + 1)

                    index_number += self.server_count

                server.create_od_startup_scripts(server_number, self.server_count)

    # __init__ arguments
    ArgInfo = ADBaseTemplate.ArgInfo + _SpecificTemplate.ArgInfo + makeArgInfo(__init__,
        PORT=Simple("Port name for the detector", str),
        BUFFERS=Simple("Maximum number of NDArray buffers to be created for plugin callbacks", int),
        MEMORY=Simple("Max memory to allocate, should be maxw*maxh*nbuffer for driver and all "
                      "attached plugins", int),
        ODIN_CONTROL_SERVER=Ident("Odin control server", _OdinControlServer),
        DATASET=Simple("Name of Dataset", str),
        DETECTOR=Simple("Detector type", str),
        ODIN_DATA_SERVER_1=Ident("OdinDataServer 1 configuration", _OdinDataServer),
        ODIN_DATA_SERVER_2=Ident("OdinDataServer 2 configuration", _OdinDataServer),
        ODIN_DATA_SERVER_3=Ident("OdinDataServer 3 configuration", _OdinDataServer),
        ODIN_DATA_SERVER_4=Ident("OdinDataServer 4 configuration", _OdinDataServer),
        ODIN_DATA_SERVER_5=Ident("OdinDataServer 5 configuration", _OdinDataServer),
        ODIN_DATA_SERVER_6=Ident("OdinDataServer 6 configuration", _OdinDataServer),
        ODIN_DATA_SERVER_7=Ident("OdinDataServer 7 configuration", _OdinDataServer),
        ODIN_DATA_SERVER_8=Ident("OdinDataServer 8 configuration", _OdinDataServer),
    )

    # Device attributes
    LibFileList = ['odinDetector']
    DbdFileList = ['odinDetectorSupport']

    def Initialise(self):
        # Configure up to 8 OdinData processes
        print "# odinDataProcessConfig(const char * ipAddress, int readyPort, " \
              "int releasePort, int metaPort)"
        for process in self.ODIN_DATA_PROCESSES:
            print "odinDataProcessConfig(\"%(IP)s\", %(READY)d, " \
                  "%(RELEASE)d, %(META)d)" % process.__dict__

        print "# odinDataDriverConfig(const char * portName, const char * serverPort, " \
              "int odinServerPort, " \
              "const char * datasetName, const char * detectorName, " \
              "int maxBuffers, size_t maxMemory)"
        print "odinDataDriverConfig(\"%(PORT)s\", \"%(CONTROL_SERVER_IP)s\", " \
              "%(CONTROL_SERVER_PORT)d, " \
              "\"%(DATASET)s\", \"%(DETECTOR_PLUGIN)s\", " \
              "%(BUFFERS)d, %(MEMORY)d)" % self.__dict__
