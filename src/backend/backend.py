"""Coordinates data reading, translation and analysis.
"""

import os
import logging
import imp
import ipc
#from mpi4py import MPI

class Backend(object):
    """Coordinates data reading, translation and analysis.
    
    This is the main class of the backend of Hummingbird. It uses a light source
    dependent translator to read and translate the data into a common format. It
    then runs whatever analysis algorithms are specified in the user provided
    configuration file.
    
    Args:
        config_file (str): The configuration file to load.        
    """
    def __init__(self, config_file):
        state = None
        conf = None
        if(config_file is None):
            # Try to load an example configuration file
            config_file = os.path.abspath(os.path.dirname(__file__)+
                                          "/../../examples/cxitut13/conf.py")
            logging.warning("No configuration file given! "
                            "Loading example configuration from %s" % (config_file))
    
        self._config_file = config_file
        # self.backend_conf = imp.load_source('backend_conf', config_file)
        Backend.conf = imp.load_source('backend_conf', config_file)
        Backend.state = Backend.conf.state
        self.translator = init_translator(Backend.state)
        print 'Starting backend...'

    def mpi_init(self):
        """Initialize MPI"""
        comm = MPI.COMM_WORLD
        self.rank = comm.Get_rank()
        print "MPI rank %d inited" % rank

    def start(self):
        """Start the event loop.
        
        Sets ``state['running']`` to True. While ``state['running']`` is True, it will
        get events from the translator and process them as fast as possible.
        """
        Backend.state['running'] = True
        while(Backend.state['running']):
            evt = self.translator.nextEvent()
            ipc.set_current_event(evt)
            Backend.conf.onEvent(evt)
            
        
def init_translator(state):
    if('Facility' not in state):
        raise ValueError("You need to set the 'Facility' in the configuration")
    elif(state['Facility'] == 'LCLS'):
        from lcls import LCLSTranslator
        return LCLSTranslator(state)
    elif(state['Facility'] == 'dummy'):
        from dummy import DummyTranslator
        return DummyTranslator(state)
    else:
        raise ValueError('Facility %s not supported' % (state['Facility']))


