# encoding: utf-8

# NECOS Network Manager
# Version: 2.1
# Author: Murillo Nunes (murillo.nns@gmail.com)
# Since: November, 2018

import time
import paramiko
from ryu.app.wsgi import ControllerBase, WSGIApplication, route, Response
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import set_ev_cls, CONFIG_DISPATCHER
from ryu.ofproto import ofproto_v1_0

# Network resources dictionary: each Icarus node is associated with a static physical port number on the Pronto switch.
resource_dictionary = {'icarus1': '1', 'icarus5': '3', 'icarus8': '5', 'icarus9': '7', 'entry1': '9', 'entry2': '13'}

# Brocade ports dictionary: relates a Pronto's physical port number with a physical port number on the Brocade switch.
control_resource_dictionary = {'1': '7', '3': '3', '5': '9', '7': '5'}

# List of currently active slices.
slices = {}

network_manager_instance_name = 'network_manager_api_app'
url = '/networkmanager/{}/'


class NetworkManager(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_0.OFP_VERSION]

    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(NetworkManager, self).__init__(*args, **kwargs)
        wsgi = kwargs['wsgi']
        wsgi.register(NetworkManagerController, {network_manager_instance_name: self})

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        self.dp = datapath
        self.parser_global = parser

    # Translates a host name (i.e. icarus1) to its corresponding physical port number in the Pronto switch.
    def host_to_port(self, host_name):
        host_port = int(resource_dictionary[host_name])
        return host_port

    # Translates a Pronto's physical port number to its corresponding port number in the Brocade switch.
    def port_translate_to_brocade(self, port_to_translate):
        brocade_port = int(control_resource_dictionary[str(port_to_translate)])
        return brocade_port

    # Creates a network slice.
    # Parameters:
    #    'slice_id' it's a unique alphanumeric identifier.
    #    'slice_members' it's a list of bare metals resources name (i.e. icarus1).
    def create_network_slice(self, slice_id, slice_members):
        if slice_id in slices:
            self.logger.warning('O SLICE ({}) JÁ EXISTE.'.format(slice_id))
        slices[slice_id] = None

        slice_port_members = [self.host_to_port(port) for port in slice_members]

        for port in slice_port_members:
            actions = []
            in_port = port

            # Remove in_port from Control VLAN
            if str(in_port) in control_resource_dictionary:
                in_port_brocade = self.port_translate_to_brocade(in_port)
                self.rem_port_from_control(in_port_brocade)

            # Creates an openflow action between each switch port and the in_port.
            for item in slice_port_members:
                if item != in_port:
                    actions.append(self.parser_global.OFPActionOutput(item))

            match = self.parser_global.OFPMatch(in_port=in_port)
            self.add_flow(self.dp, 1, match, actions)

        self.logger.info('INFO - SLICE ID: {}'.format(slice_id))
        self.logger.info('INFO - SLICE MEMBERS: {}'.format(slice_members))

    # Deletes a network slice.
    # Parameters:
    #    'slice_id' it's a unique alphanumeric identifier.
    #    'slice_members' it's a list of bare metals resources name (i.e. icarus1).
    def delete_network_slice(self, slice_id, slice_members):
        if slice_id in slices:
            del slices[slice_id]
        else:
            self.logger.warning('O SLICE ({}) NÃO EXISTE.'.format(slice_id))

        slice_port_members = [self.host_to_port(port) for port in slice_members]

        for port in slice_port_members:
            in_port = port

            # Add in_port to Control VLAN
            if str(in_port) in control_resource_dictionary:
                in_port_brocade = self.port_translate_to_brocade(in_port)
                self.add_port_to_control(in_port_brocade)

            match = self.parser_global.OFPMatch(in_port=in_port)
            self.rem_flow(self.dp, match)

        self.logger.info('INFO - SLICE ID: {}'.format(slice_id))
        self.logger.info('INFO - SLICE MEMBERS: {}'.format(slice_members))

    # Adds an OpenFlow routing rule to the Pronto switch.
    # Parameters:
    #    'datapath' it's the switch.
    #    'priority' it's an integer that defines the priority order for the rule.
    #    'match' it's the expected attribute for the actions.
    #    'actions' it's the actions to be done when matching are true.
    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        mod = parser.OFPFlowMod(datapath=datapath,
                                match=match,
                                idle_timeout=0,
                                hard_timeout=0,
                                priority=priority,
                                actions=actions)
        datapath.send_msg(mod)

    # Removes an OpenFlow routing rule from the Pronto switch.
    # Parameters:
    #    'datapath' it's the switch.
    #    'match' it's the expected attribute for the actions.
    def rem_flow(self, datapath, match):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        mod = parser.OFPFlowMod(datapath=datapath,
                                match=match,
                                command=ofproto.OFPFC_DELETE)
        datapath.send_msg(mod)

    # THE METHODS BELOW CONNECTS/DISCONNECTS THE VLAN CONTROL TO THE BROCADE SWITCH
    def rem_port_from_control(self, port_to_remove):
        self.open_ssh_connection()
        command = 'enable\nconfig t\nvlan 444\nno untagged ethernet 1/{}\nwrite memory\nexit\n'.format(port_to_remove)
        self.send_cmd_to_brocade(command)
        self.close_ssh_connection()

    def add_port_to_control(self, port_to_add):
        self.open_ssh_connection()
        command = 'enable\nconfig t\nvlan 444\nuntagged ethernet 1/{}\nwrite memory\nexit\n'.format(port_to_add)
        self.send_cmd_to_brocade(command)
        self.close_ssh_connection()

    def open_ssh_connection(self):
        ip_switch_brocade = '10.138.1.11'
        global brocade

        brocade = paramiko.SSHClient()
        brocade.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        brocade.connect(ip_switch_brocade, username='dpdadm', password='dpdadm')

    def close_ssh_connection(self):
        brocade.close()

    def send_cmd_to_brocade(self, cmd):
        brocade_shell = brocade.invoke_shell()
        brocade_shell.send(cmd)
        time.sleep(2)


class NetworkManagerController(ControllerBase):

    def __init__(self, req, link, data, **config):
        super(NetworkManagerController, self).__init__(req, link, data, **config)
        self.network_manager_app = data[network_manager_instance_name]

    @route('networkmanager', url.format('create_slice'), methods=['POST'])
    def create_slice(self, req, **kwargs):
        try:
            new_entry = req.json if req.body else {}
        except ValueError:
            raise Response(status=400)

        slice_id = new_entry['slice_id']
        slice_ports = new_entry['ports']

        self.network_manager_app.create_network_slice(slice_id, slice_ports)

        return Response(status=200)

    @route('networkmanager', url.format('delete_slice'), methods=['DELETE'])
    def delete_slice(self, req, **kwargs):
        try:
            new_entry = req.json if req.body else {}
        except ValueError:
            raise Response(status=400)

        slice_id = new_entry['slice_id']
        slice_ports = new_entry['ports']

        self.network_manager_app.delete_network_slice(slice_id, slice_ports)

        return Response(status=200)
