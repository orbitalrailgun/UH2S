import json
import ipaddress
#from netbox import Netbox
import syslog
from app.logging import currentTimestamp, get_log_message, logger_log, currentFuncName
from app.sources.additional.flatten import flatten_data

def execute_netbox_search_cidr_by_ipaddress(parameters, source_object, data_map, current_state):
    import requests
    try:
        query = parameters
        source = source_object
        
        headers = {
            'Authorization': f'Token {source["key"]["value"]}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        response = requests.get( f'{source["url"]}/api/ipam/prefixes/?contains={query["target"]}', headers=headers)

        if response.status_code != 200:
            return False, f"response.status_code is not 200: {response.status_code}", currentFuncName(), []
        
        response_data = response.json()

        if response_data['count'] <= 0:
            return True, f"Not found", currentFuncName(), []
        
        if "results" not in response_data:
            return False, f"Results node not found", currentFuncName(), []
        
        base_depth = -1
        data = []
        for result in response_data["results"]:
            if "_depth" not in result:
                continue
            if result["_depth"] > base_depth:
                data = [flatten_data(result)]
                base_depth = result["_depth"]
        
        if len(data) == 1:
            return True, "ОК", currentFuncName(), data
        else:
            return False, "_depth in result not found?", currentFuncName(), data

    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []

def netbox_finder(target, url, token_netbox, fast_mode):
    import requests
    netbox = {'target': target}
    potential_contacts = set()
    try:
        headers = {
            'Authorization': f'Token {token_netbox}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        response = requests.get( f'{url}/api/ipam/ip-addresses/?q={target}/', headers=headers)

        if response.status_code != 200:
            return False, f"response.status_code is not 200: {response.status_code}", currentFuncName(), []
        
        response_data = response.json()

        if response_data['count'] <= 0:
            return True, f"Not found", currentFuncName(), []
        
        result = response_data['results'][0]

        if result.get('description'):
            netbox['description'] = result['description']

        device_url = ''
        if result["assigned_object"]:
            if 'device' in result["assigned_object"]:
                netbox['hostname'] = result["assigned_object"]['device']['display']
                device_url = f'{url}/api/dcim/devices/?q={netbox["hostname"]}'

            elif 'virtual_machine' in result["assigned_object"]:
                netbox['hostname'] = result["assigned_object"]['virtual_machine']['display']
                device_url = f'{url}/api/virtualization/virtual-machines/?q={netbox["hostname"]}'

            if device_url:
                device_response = requests.get(device_url, headers=headers).json()
                if device_response['count'] > 0:
                    device_result = device_response['results'][0]
                    # тут неплохо было бы отдельно приносить список custom_fields в query и пробегаться по нему
                    if device_result['description']:
                                    netbox['description_of_assignment'] = device_result['description']
                    if device_result['custom_fields'].get('owner'):
                                    netbox['owner'] = device_result['custom_fields']['owner']
                    if device_result['custom_fields'].get('Service owner'):
                                    netbox['service owner'] = device_result['custom_fields']['Service owner']
                    if device_result['custom_fields'].get('team'):
                                    netbox['team'] = device_result['custom_fields']['team']
                    if device_result['custom_fields'].get('product'):
                                    netbox['product'] = device_result['custom_fields']['product']
                    if device_result['custom_fields'].get('project'):
                                    netbox['project'] = device_result['custom_fields']['project']
                    if device_result['custom_fields'].get('cluster'):
                                    netbox['cluster'] = device_result['custom_fields']['cluster']
                    if device_result['custom_fields'].get('ssh_custom_key'):
                                    netbox['ssh_custom_key'] = device_result['custom_fields']['ssh_custom_key']
                    if device_result['custom_fields'].get('ssh_keys_groups'):
                                    netbox['ssh_keys_groups'] = device_result['custom_fields']['ssh_keys_groups']
                    if device_result['custom_fields'].get('ssh_keys_groups'):
                                    netbox['ssh_keys_groups'] = device_result['custom_fields']['ssh_keys_groups']
                    if device_result['custom_fields'].get('project_name'):
                                    netbox['project_name'] = device_result['custom_fields']['project_name']
                    if device_result['custom_fields'].get('env'):
                                    netbox['env'] = device_result['custom_fields']['env']
                    if fast_mode == False:
                                contacts_response = requests.get(
                                    f'{url}/api/tenancy/contact-assignments/?object_id={device_result["id"]}',
                                    headers=headers
                                )

                                if contacts_response.status_code == 200:
                                    contacts_data = contacts_response.json()

                                    if contacts_data['count'] > 0:
                                        for contact in contacts_data['results']:
                                            potential_contacts.add(contact['contact']['display'])
        if fast_mode == False:
            way = []
            prefix_response = requests.get(f'{url}/api/ipam/prefixes/?q={target}', headers=headers)
            if prefix_response.status_code == 200:
                prefix_data = prefix_response.json()

                if prefix_data['count'] > 0:
                    for prefix in prefix_data['results']:
                        if prefix.get('description'):
                            way.append(f'{prefix["display"]} ({prefix["description"]})')
                        else:
                            way.append(f'{prefix["display"]} (No info)')

                    netbox['description_of_subnet'] = ' -> '.join(way).lower()

            else:
                netbox['errors'] = f'{prefix_response.status_code}: Netbox search is not performed'
            netbox['potential_contacts'] = ', '.join(sorted(potential_contacts))

    except requests.exceptions.RequestException as e:
            return False, f'Error connecting to Netbox API: {e}', currentFuncName(), []

    except KeyError as e:
            return False, f'KeyError: {e}. Check configuration or parameters.', currentFuncName(), []

    except Exception as e:
            return False, f'Unexpected error occurred: {e}', currentFuncName(), []
    
    return True, f"OK", currentFuncName(), [{k: v for k, v in netbox.items() if v}]


#def execute_netbox_finder(data_map, source, query, step, parameters, current_state):
def execute_netbox_finder(parameters, source_object, data_map, current_state):
    query = parameters
    source = source_object
    logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
    try:
        netbox_finder_result = netbox_finder(query["target"], f"{source["url"]}", source["key"]["value"], query["fast_flag"]) # раньше был source["url"][0]
        if netbox_finder_result[0] == False:
                error_message = f"netbox_finder fail: {netbox_finder_result[1]}"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                return False, error_message, currentFuncName(), []
        logger_log(syslog.LOG_DEBUG, get_log_message("done", currentFuncName(), current_state))
        return True, "ОК", currentFuncName(), netbox_finder_result[3]
    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []