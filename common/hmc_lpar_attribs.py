'''This script connects to the HMC API and queries for profile data'''

import atexit
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET
import requests
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning
import yaml
from rich.console import Console

# rich colours
console = Console()

# Set the base path to be used for reading files from the local filesystem
base_path = Path(__file__).parent

# Read configuration file settings
cfgfile = (base_path / '../config/config.yaml').resolve()
try:
    with open(cfgfile, encoding='utf-8', mode='r') as cfgfile:
        cfg = yaml.load(cfgfile, Loader=yaml.FullLoader)
except (NameError, FileNotFoundError):
    console.print('[red]Configuration file not found.[/red]')
    sys.exit()

# Class definition
class HMC:
    '''Class to interact with the HMC API.'''
    def __init__(self, hmc, user, passwd):
        '''Class initialisation is used to login to the HMC
        registers the clean up function for automatic log-off even if the python program crashes
        Arguments: 1=hostname of HMC or IP address, 2=HMC username, 3=HMC user password
        Returns: the HMC object'''
        self.hmc_name = ''
        self.token = ''
        self.ssl_verify = cfg['ssl_verify']
        self.debug = cfg['debug']
        self.connected = False
        self.logon(hmc, user, passwd)
        atexit.register(self.cleanup)

    def save_to_file(self, filename, content):
        '''Internal function: Saves output to filesystem for debugging.
        Arguements: 1=Filename to write to, 2=Contents to write to file
        Returns: None '''
        debugdir = (base_path / 'debug').resolve()
        if not debugdir.is_dir():
            console.log(f'DEBUG: Creating debug directory {debugdir}')
            os.mkdir(debugdir)
        filename = f'{debugdir}/{filename}'
        with open(filename, encoding='utf-8', mode='w') as xml_debug:
            print(content, file=xml_debug)

    def cleanup(self):
        '''Internal function: Logoff the HMC if the user doesn't, so we don't leave stale sessions running
        Arguments: None
        Returns = Never'''
        if self.connected:
            logoff_headers = {'X-API-Session': self.token}
            logoff_url = f'https://{self.hmc_name}:12443/rest/api/web/Logon'
            requests.delete(logoff_url, headers=logoff_headers, verify=False)

    def check_connected(self, context):
        '''Internal function: Sanity check that we have logged on to the HMC
        Arguments: 1=String to explain what the module is doing - only used to report the error
        Returns: Never if there is no connection'''
        if self.connected is False:
            console.print(f'[red]Attempt to {context} when not logged on. Halting![/red]')
            sys.exit(42)

    def logon(self, hmc, user, passwd):
        '''Internal function: Called from the class initialisation
        Arguments: Same as class initialisation
        a) set up put request to the HMC for log-on
        b) check the status as username/password or even HMC details can be wrong
        c) if logon fails exit - there is nothing further that can be done
        d) convert returned text to XML and extract the authorisation token
        e) return the token which is used for all subsequent HMC interactions'''
        if self.connected:
            console.print('[red]Attempt to logon when already logged on. Halting![/red]')
            sys.exit(42)
        if self.debug:
            console.log('DEBUG:logon()')
            console.log("DEBUG:Switching off ugly Security Warnings 'Unverified HTTPS request is being made'")
        # HMC appears not to have a genuine recognised CA certficate
        # HMC Users can set that up if they desire
        if not self.ssl_verify:
            disable_warnings(InsecureRequestWarning)

        self.hmc_name = hmc
        user = escape(user)
        passwd = escape(passwd)
        logonheaders = {'Content-Type': 'application/vnd.ibm.powervm.web+xml; type=LogonRequest'}
        logon_url = f'https://{self.hmc_name}:12443/rest/api/web/Logon'
        logon_payload = f'<LogonRequest schemaVersion="V1_0" xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/" xmlns:mc="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/"><UserID>{user}</UserID><Password>{passwd}</Password></LogonRequest>'
        response = requests.put(logon_url, data=logon_payload, headers=logonheaders, verify=False)
        if response.status_code != 200:
            console.print('[red]Error: HMC logon failed[/red]')
            if self.debug:
                console.log(f'DEBUG: Logon failed error code={response.status_code} url={logon_url}')
                console.log(f'DEBUG: Returned:{response.text}')
            # Do not return if we failed to logon
            sys.exit(response.status_code)
        # Extract token from the returned XML
        xml_response = ET.fromstring(response.text)
        self.token = xml_response[1].text
        if self.debug:
            console.log(f'DEBUG: Log on response={response.status_code} and got Token=\n----\n{self.token}\n----')
        self.connected = True

    def logoff(self):
        '''Disconnect from the HMC. This is actually a HTTP delete request (delete the token)
        Arguments: None
        Returns:
        if the logoff fails this function exits the program
        if the logoff works it returns nothing'''
        if self.debug:
            console.log('DEBUG:logoff()')
        self.check_connected('logoff')
        logoff_headers = {'X-API-Session': self.token }
        logoff_url = f'https://{self.hmc_name}:12443/rest/api/web/Logon'
        response = requests.delete(logoff_url, headers=logoff_headers, verify=False)
        rcode = response.status_code
        # delete can respond with these three good values
        ok_return_codes = [200, 202, 204]
        if rcode in ok_return_codes:
            if self.debug:
                console.log(f'DEBUG: Successfully disconnected from {self.hmc_name} (code={rcode})')
            self.connected = False
        else:
            console.print(f'[red]Error: Logoff failed error code={rcode} url={logoff_url} data={response.text}[/red]')
            sys.exit(rcode)

    def get_lpar_config(self, lpar):
        '''a) searches for LPAR on HMC
        b) parses XML data returned from HMC to populate dictionary (HTTP rcode=200)
        c) returns 1 if LPAR not found (HTTP rcode=204), or 2 if HTTP rcode is anything else
        Arguments: LPAR name
        Returns: Dictionary containing LPAR attributes'''
        # Check if connected to the HMC
        self.check_connected('get_lpar_config')

        # Search HMC for LPAR
        gen_prof_headers = {'Content-Type': 'application/xml', 'Accept': 'application/vnd.ibm.powervm.uom+xml', 'Type': 'LogicalPartition', 'X-API-Session': self.token}
        gen_prof_url = f'https://{self.hmc_name}:12443/rest/api/uom/LogicalPartition/search/(PartitionName=={lpar})'
        response = requests.get(gen_prof_url, headers=gen_prof_headers, verify=False)
        rcode = response.status_code

        # Validate return code
        if rcode == 200:
            # LPAR found on HMC
            # Initialise dictionary to store attribute values
            lpardata = {}

            # Lists of attributes that we care about
            attribs_general_general = ['PartitionType', 'CurrentProcessorCompatibilityMode']
            attribs_default_general = ['ProfileName']
            attribs_default_processor = ['SharingMode', 'UncappedWeight', 'MinimumProcessingUnits', 'DesiredProcessingUnits', 'MaximumProcessingUnits', 'MinimumVirtualProcessors', 'DesiredVirtualProcessors', 'MaximumVirtualProcessors']
            attribs_default_memory = ['ActiveMemoryExpansionEnabled', 'DesiredMemory', 'ExpansionFactor', 'MaximumMemory', 'MinimumMemory']
            attribs_default_veth = ['VirtualSlotNumber', 'PortVLANID', 'VirtualSwitchName']
            attribs_default_vfc = ['VirtualSlotNumber', 'AdapterType']
            attribs_default_vscsi = ['VirtualSlotNumber', 'AdapterType']

            # Parse XML response from HMC
            ns = {'ns0': 'http://www.w3.org/2005/Atom',
                  'ns1': 'http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/'}
            xml_response = ET.fromstring(response.text)

            # Debug
            if self.debug:
                console.log(f'DEBUG: Writing response out to debug directory [{lpar}_general.xml]')
                self.save_to_file(f'{lpar}_general.xml', ET.tostring(xml_response, encoding='utf-8').decode('utf8'))

            # There is some useful data in the general XML file returned from a basic LPAR search, so lets parse
            # that and add it to the dict.
            # General attributes
            if cfg['compare_general']:
                for attrib in attribs_general_general:
                    if xml_response.find(f'.//ns1:{attrib}', ns) is not None:
                        lpardata[f'General_{attrib}'] = xml_response.find(f'.//ns1:{attrib}', ns).text
                    else:
                        lpardata[f'General_{attrib}'] = None

            # All other LPAR attributes we'll get from the default profile
            if xml_response.find('.//ns1:AssociatedPartitionProfile', ns) is not None:
                def_prod_url = xml_response.find('.//ns1:AssociatedPartitionProfile', ns).attrib['href']
            else:
                return 3

            # Get default profile data
            response = requests.get(def_prod_url, headers=gen_prof_headers, verify=False)
            rcode = response.status_code
            if rcode == 200:
                # Default profile found for LPAR
                # Parse XML response from HMC
                xml_response = ET.fromstring(response.text)

                # Debug
                if self.debug:
                    console.log(f'DEBUG: Writing response out to debug directory [{lpar}_default_profile.xml]')
                    self.save_to_file(f'{lpar}_default_profile.xml', ET.tostring(xml_response, encoding='utf-8').decode('utf8'))

                # General attribues
                if cfg['compare_general']:
                    for attrib in attribs_default_general:
                        if xml_response.find(f'.//ns1:{attrib}', ns) is not None:
                            lpardata[f'General_{attrib}'] = xml_response.find(f'.//ns1:{attrib}', ns).text
                        else:
                            lpardata[f'General_{attrib}'] = None

                # Processor attributes
                if cfg['compare_processors']:
                    for attrib in attribs_default_processor:
                        if xml_response.find(f'.//ns1:{attrib}', ns) is not None:
                            lpardata[f'Processor_{attrib}'] = xml_response.find(f'.//ns1:{attrib}', ns).text
                        else:
                            lpardata[f'Processor_{attrib}'] = None

                # Memory attributes
                if cfg['compare_memory']:
                    for attrib in attribs_default_memory:
                        if xml_response.find(f'.//ns1:{attrib}', ns) is not None:
                            lpardata[f'Memory_{attrib}'] = xml_response.find(f'.//ns1:{attrib}', ns).text
                        else:
                            lpardata[f'Memory_{attrib}'] = None

                # Virtual Ethernet Adapter attributes
                if cfg['compare_networking']:
                    index = 0
                    for adapter in xml_response.findall('.//ns1:ProfileClientNetworkAdapter', ns):
                        for attrib in attribs_default_veth:
                            lpardata[f'Network_VirtualEthAdapter_{index}_{attrib}'] = adapter.find(f'ns1:{attrib}', ns).text
                        index += 1

                # Virtual FC Adapter attributes
                if cfg['compare_virtual_fc']:
                    index = 0
                    for adapter in xml_response.findall('.//ns1:ProfileVirtualFibreChannelClientAdapter', ns):
                        for attrib in attribs_default_vfc:
                            lpardata[f'vFC_VirtualFcAdapter_{index}_{attrib}'] = adapter.find(f'ns1:{attrib}', ns).text
                        index += 1

                # Virtual SCSI Adapter attributes
                if cfg['compare_virtual_scsi']:
                    index = 0
                    for adapter in xml_response.findall('.//ns1:ProfileVirtualSCSIClientAdapter', ns):
                        for attrib in attribs_default_vscsi:
                            lpardata[f'vSCSI_VirtualScsiAdapter_{index}_{attrib}'] = adapter.find(f'ns1:{attrib}', ns).text
                        index += 1

                return lpardata
            return 4
        elif rcode == 204:
            # LPAR not found on HMC
            if self.debug:
                console.log(f'DEBUG: LPAR not found (hmc; {self.hmc_name} lpar; {lpar} response; {rcode}).')
            return 1
        else:
            # A return code other than 200 or 204 returned
            if self.debug:
                console.log(f'DEBUG: LPAR query error (hmc; {self.hmc_name} lpar; {lpar} response; {rcode}).')
            return 2
