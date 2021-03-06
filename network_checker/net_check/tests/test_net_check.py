#    Copyright 2014 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import json
import multiprocessing
import os
import signal
import socket
import time
import unittest

import pcap
from scapy import all as scapy

from net_check import api


class BaseListenerTestCase(unittest.TestCase):

    def setUp(self, config=None):
        default_config = {
            "src": "1.0.0.0", "ready_port": None,
            "ready_address": "localhost", "dst": "1.0.0.0",
            "interfaces": {"eth0": "0,100,100,101,102,103,104,105,106,107"},
            "action": "listen",
            "cookie": "Nailgun:", "dport": 31337, "sport": 31337,
            "src_mac": None, "dump_file": "/var/tmp/net-probe-dump"
        }
        self.config = config or default_config
        self.start_socket()
        listener = api.Listener(self.config)
        self.listener = multiprocessing.Process(target=listener.run)
        self.listener.start()

        connection, address = self.ready_socket.accept()
        request = connection.recv(1024)
        self.assertEqual('READY', request.decode())
        connection.close()
        self.ready_socket.shutdown(socket.SHUT_RDWR)
        self.ready_socket.close()

        self.send_packets()
        os.kill(self.listener.pid, signal.SIGINT)
        self.listener.join()

    def start_socket(self):
        self.ready_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.ready_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ready_socket.bind((self.config['ready_address'], 0))
        self.config['ready_port'] = self.ready_socket.getsockname()[1]
        self.ready_socket.listen(1)
        self.ready_socket.settimeout(5)

    def send_packets(self):
        pass

    def tearDown(self):
        self.ready_socket.close()
        self.listener.terminate()
        if os.path.exists(self.config['dump_file']):
            os.unlink(self.config['dump_file'])


class TestCaseListenerPcapFile(BaseListenerTestCase):

    def send_packets(self):
        directory_path = os.path.dirname(__file__)
        scapy_data = scapy.rdpcap(os.path.join(directory_path, 'vlan.pcap'))
        for p in scapy_data:
            scapy.sendp(p, iface='eth0')

    def test_listener_pcap_file(self):

        with open(self.config['dump_file'], 'r') as f:
            data = json.loads(f.read())

        self.assertEqual(data, {u'eth0': {
            u'102': {u'1': [u'eth0'], u'2': [u'eth0']},
            u'103': {u'1': [u'eth0'], u'2': [u'eth0']},
            u'100': {u'1': [u'eth0'], u'2': [u'eth0']},
            u'101': {u'1': [u'eth0'], u'2': [u'eth0']},
            u'106': {u'1': [u'eth0'], u'2': [u'eth0']},
            u'107': {u'1': [u'eth0'], u'2': [u'eth0']},
            u'104': {u'1': [u'eth0'], u'2': [u'eth0']},
            u'105': {u'1': [u'eth0'], u'2': [u'eth0']}}})


class TestCaseListenerCorruptedData(BaseListenerTestCase):

    def send_packets(self):
        normal_data = 'Nailgun:eth0 2'
        corrupted_data = normal_data + '7h 7\00\00\00'
        message_len = len(normal_data) + 8
        p = scapy.Ether(src=self.config['src_mac'],
                        dst="ff:ff:ff:ff:ff:ff")
        p = p / scapy.IP(src=self.config['src'], dst=self.config['dst'])
        p = p / scapy.UDP(sport=self.config['sport'],
                          dport=self.config['dport'],
                          len=message_len) / corrupted_data
        for i in xrange(5):
            scapy.sendp(p, iface='eth0')

    def test_listener_corrupted_data(self):

        with open(self.config['dump_file'], 'r') as f:
            data = json.loads(f.read())

        self.assertEqual(data, {u'eth0': {u'0': {u'2': [u'eth0']}}})


class TestNetCheckSender(unittest.TestCase):

    def setUp(self):
        directory_path = os.path.dirname(__file__)
        self.scapy_data = scapy.rdpcap(os.path.join(directory_path,
                                                    'vlan.pcap'))
        self.config = {
            "src": "1.0.0.0", "ready_port": 31338,
            "ready_address": "localhost", "dst": "1.0.0.0",
            "interfaces": {"eth0": "0,100,101,102,106,107,108"},
            "action": "listen",
            "cookie": "Nailgun:", "dport": 31337, "sport": 31337,
            "src_mac": None,
            "dump_file": "/var/tmp/net-probe-dump",
            "uid": "2"
        }

    def start_pcap_listener(self):
        self.pcap_listener = pcap.pcap('eth0')
        self.vlan_pcap_listener = pcap.pcap('eth0')
        filter_string = 'udp and dst port {0}'.format(self.config['dport'])
        self.vlan_pcap_listener.setfilter('vlan and {0}'.format(filter_string))
        self.pcap_listener.setfilter(filter_string)

    def start_sender(self):
        sender = api.Sender(self.config)
        self.sender = multiprocessing.Process(target=sender.run)
        self.sender.start()

    @property
    def pcap_packets(self):
        for pkt in self.pcap_listener.readpkts():
            yield pkt
        for pkt in self.vlan_pcap_listener.readpkts():
            yield pkt

    @property
    def received_vlans(self):
        results = set()
        for pkt in self.pcap_packets:
            ether = scapy.Ether(pkt[1])
            if scapy.Dot1Q in ether:
                vlan = str(ether[scapy.Dot1Q].vlan)
            else:
                vlan = '0'
            results.update([vlan])
        return results

    def test_sender(self):
        self.start_pcap_listener()
        self.start_sender()
        time.sleep(3)
        self.sender.join()

        expected_vlans = set(self.config['interfaces']['eth0'].split(','))
        self.assertEqual(expected_vlans, self.received_vlans)
