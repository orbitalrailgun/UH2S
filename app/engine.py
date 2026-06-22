import re
import json
import syslog
from app.logging import get_log_message, logger_log, currentFuncName
from app.validation import json_validate

# импорт источников
from app.sources.netbox import execute_netbox_finder, execute_netbox_search_cidr_by_ipaddress
from app.sources.elastic import execute_elasctic_query_via_client, execute_elasctic_aggs_via_client, execute_function_linux_pid_hierarchy_elastic, execute_function_linux_pid_siblings_elastic
from app.sources.elastic_requests import execute_elastic_query as execute_elasctic_query_via_requests, execute_elastic_aggs as execute_elasctic_aggs_via_requests, execute_function_linux_pid_hierarchy_elastic_requests, execute_function_linux_pid_siblings_elastic_requests
from app.sources.opensearch import execute_opensearch_query, execute_opensearch_aggs
from app.sources.postgresql import execute_postgresql 
from app.sources.sqlite3 import execute_sqlite3
from app.sources.mssql import execute_mssql
from app.sources.ollama import execute_ollama_chat_query
from app.sources.llama import execute_llama_chat_query
from app.sources.pandas import execute_pandas_dynamic_aggregation, execute_pandas_aggregation, execute_pandas_aggregation_with_time_grouper, execute_pandas_shift, execute_pandas_union
#from app.sources.grafana import execute_grafana_export_table_requests
from app.sources.youtrack import execute_youtrack_project_finder, execute_youtrack_all_project_issue_finder, execute_youtrack_all_articles_finder
from app.sources.gitlab import execute_gitlab_namespace_owner_request, execute_gitlab_search_request
from app.sources.iris import execute_function_iris_get_alerts
from app.sources.thehive import execute_thehive_get_alerts
#from app.sources.teleport import execute_function_get_hosts_teleport
from app.sources.dns import execute_dns_resolve
from app.sources.mysql import execute_mysql
from app.sources.manticoresearch import execute_manticoresearch_sql
from app.sources.duckdb import execute_duckdb
from app.sources.universal_harvester import execute_local_scenario

from app.notify import notify_mattermost_proc, notify_telegram_proc


NOTIFY_MAP = {
    "mattermost":{
        "send":notify_mattermost_proc
    },
    "telegram":{
        "send":notify_telegram_proc
    }
}

def get_notifier_function(notifier_type, current_state:dict):
    if notifier_type not in NOTIFY_MAP:
        error_message = f"there is not notifier type {notifier_type} in NOTIFY_MAP"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), {}
    
    function_object = NOTIFY_MAP[notifier_type]["send"]
    
    return True, "Ok", currentFuncName(), function_object

ENGINE_SOURCES_AND_FUNCTIONS_MAP = {
    "elastic":{
        "functions":{
            "generic_query":{
                "required":{
                    "index":"example: events-*",
                    "query":"",
                    "fields":"",
                    "sort":"",
                    "size":"",
                    "search_after_shift":-10
                },
                "functions":{
                    "query": execute_elasctic_query_via_client,
                    #"converter":lambda: None
                }
            },
            "aggs_query":{
                "required":{
                    "index":"example: events-*",
                    "query":"",
                    "aggs":"",
                    # "sort":"",
                    # "size":"",
                    # "search_after_shift":-10
                },
                "functions":{
                    "query": execute_elasctic_aggs_via_client,
                    #"converter":lambda: None
                }
            },
            "pid_hierarchy":{
                "required":{
                    "index":"example: events-*",
                    "query":"",
                    "fields":"",
                    "sort":"",
                    "size":"",
                    "search_after_shift":-10
                },
                "functions":{
                    "query": execute_function_linux_pid_hierarchy_elastic,
                    #"converter":lambda: None
                }
            },
            "pid_siblings":{
                "required":{
                    "index":"example: events-*",
                    "query":"",
                    "fields":"",
                    "sort":"",
                    "size":"",
                    "search_after_shift":-10
                },
                "functions":{
                    "query": execute_function_linux_pid_siblings_elastic,
                    #"converter":lambda: None
                }
            }
        },
        "required":{
            "host":"https://elastic.example.ru",
            "port":9201,
            "auth_type":"api_key",# or http_auth
            "max_threads":10
        },
        "unrequired":{
            "verify_certs":False,
            "request_timeout":300,
            "max_retries":2,
            "retry_on_timeout":True,
            "ssl_show_warn":False
        }
    },
    "elastic_requests":{
        "functions":{
            "query":{
                "required":{
                    "url":"https://elastic.ru/api/console/proxy?path=/%(index)s/_search?batched_reduce_size=64&method=POST",
                    "query":{},
                    "fields":[],
                    "sort":[],
                    #"size":1000, # опционально
                    "limit":-1,
                    #"search_after_shift":-10 # опционально
                },
                "unrequired":{
                    "size":1000, # опционально
                    "limit":-1, # опционально
                    "search_after_shift":-10 # опционально
                },
                "functions":{
                    "query": execute_elasctic_query_via_requests,
                    #"converter":lambda: None
                }
            },
            "aggs_query":{
                "required":{
                    "url":"https://elastic.ru/api/console/proxy?path=/%(index)s/_search?batched_reduce_size=64&method=POST",
                    "query":{},
                    "aggs":{},
                },
                "functions":{
                    "query": execute_elasctic_aggs_via_requests,
                    #"converter":lambda: None
                }
            },
            "pid_hierarchy":{
                "required":{
                    "url":"https://elastic.ru/api/console/proxy?path=/%(index)s/_search?batched_reduce_size=64&method=POST",
                    "query":"",
                    "fields":"",
                    "sort":"",
                    "size":"",
                    "search_after_shift":-10
                },
                "functions":{
                    "query": execute_function_linux_pid_hierarchy_elastic_requests,
                    #"converter":lambda: None
                }
            },
            "pid_siblings":{
                "required":{
                    "url":"https://elastic.ru/api/console/proxy?path=/%(index)s/_search?batched_reduce_size=64&method=POST",
                    "query":"",
                    "fields":"",
                    "sort":"",
                    "size":"",
                    "search_after_shift":-10
                },
                "functions":{
                    "query": execute_function_linux_pid_siblings_elastic_requests,
                    #"converter":lambda: None
                }
            }
        },
        "required":{
            "max_threads":10
        },
        "unrequired":{
            "verify_certs":False,
            "request_timeout":300
        }
    },
    "opensearch":{
        "functions":{
            "generic_query":{
                "required":{
                    "index":"example: events-*",
                    "query":"",
                    "fields":"",
                    "sort":"",
                    "size":"",
                    "search_after_shift":-10
                },
                "functions":{
                    "query": execute_opensearch_query,
                    #"converter":lambda: None
                }
            },
            "aggs_query":{
                "required":{
                    "index":"example: events-*",
                    "query":"",
                    "aggs":"",
                },
                "functions":{
                    "query": execute_opensearch_aggs,
                    #"converter":lambda: None
                }
            },
        },
        "required":{
            "host":"opensearch.example.ru",
            "port":9200,
            "auth_type":"http_auth",
            "max_threads":10
        },
        "unrequired":{
            "http_compress":True,
            "use_ssl":True,
            "verify_certs":False,
            "ssl_assert_hostname":False,
            "ssl_show_warn":False,
            "timeout":300, 
            "max_retries":2 
        }
    },
    "netbox":{
        "functions":{
            "finder":{
                "required":{
                    "target":"127.0.0.1",
                    "fast_flag":False
                },
                "functions":{
                    "query": execute_netbox_finder,
                    #"converter": lambda: None
                }
            },
            "search_cidr_by_ip":{
                "required":{
                    "target":"127.0.0.1"
                },
                "functions":{
                    "query": execute_netbox_search_cidr_by_ipaddress,
                    #"converter": lambda: None
                }
            }
        }, 
        "required":{
            "url":"https://netbox.example.ru",
            "host":"netbox.example.ru",
            "port":443,
            #"auth_type":"api_key",
            "timeout": 60,
            "max_threads":10
        }, 
        "unrequired":{
            "use_ssl":True
        }
    },
    "manticoresearch":{
        "functions":{
            "sql_query":{
                "required":{
                    "query":"SHOW TABLES"
                },
                "functions":{
                    "query": execute_manticoresearch_sql,
                    #"converter": lambda: None
                }
            }
        }, 
        "required":{
            "url":"https://manticoresearch.example.ru:9308/sql?mode=raw",
            "timeout": 60,
            "max_threads":10
        }, 
        "unrequired":{
            "verify":False
        }
    },
    "sqlite3_im":{
        "functions":{
            "query":{
                "required":{
                    "queries":["SQL query 1","SELECT * FROM anytable;"],
                },
                "functions":{
                    "query": execute_sqlite3,
                    #"converter": lambda: None
                }
            }
        }, 
        "required":{}, 
        "unrequired":{}
        },
    "duckdb_im":{
        "functions":{
            "query":{
                "required":{
                    "queries":["SQL query 1","SELECT * FROM anytable;"],
                    "type":"table"# or view
                },
                "functions":{
                    "query": execute_duckdb,
                    #"converter": lambda: None
                }
            }
        }, 
        "required":{}, 
        "unrequired":{}
        },
    "postgresql":{
        "functions":{
            "query":{
                "required":{
                    "preparatory_queries":["SQL query 1","SQL query 2"],
                    "final_query":"SELECT * FROM anytable;",
                    "timeout":180
                },
                "functions":{
                    "query": execute_postgresql,
                    #"converter": lambda: None
                }
            }
        }, 
        "required":{
            "host":"postgresql.example.ru",
            "port":5432,
            "database":"db",
            "auth_type":"login/pass",
            "max_threads":10
        }, 
        "unrequired":{}
    },
    "mysql":{
        "functions":{
            "query":{
                "required":{
                    "preparatory_queries":["SQL query 1","SQL query 2"],
                    "final_query":"SELECT * FROM anytable;",
                    "timeout":180
                },
                "functions":{
                    "query": execute_mysql,
                    #"converter": lambda: None
                }
            }
        }, 
        "required":{
            "host":"mysql.example.ru",
            "port":3306,
            "database":"db",
            "auth_type":"login/pass",
            "max_threads":10
        }, 
        "unrequired":{
            # убедитесь, что эти файлы лежат в storage, обычно storage_path='/srv/storage' 
            "ca.pem":"/mysql/ssl/ca.pem",
            "client-cert.pem":"/mysql/ssl/client-cert.pem",
            "client-key.pem":"/mysql/ssl/client-key.pem"
        }
    },
    "mssql":{
        "functions":{
            "query":{
                "required":{
                    "preparatory_queries":["SQL query 1","SQL query 2"],
                    "final_query":"SELECT * FROM anytable;",
                    "timeout":180,
                    "encoding":"latin-1"
                },
                "functions":{
                    "query": execute_mssql,
                    #"converter": lambda: None
                }
            }
        }, 
        "required":{
            "host":"mssql.example.ru",
            "port":5000,
            "database":"db",
            "auth_type":"login/pass",
            "max_threads":10
        }, 
        "unrequired":{}
    },
    "dns":{
        "functions":{
            "query":{
                "required":{},
                "functions":{
                    "query": execute_dns_resolve,
                    #"converter": lambda: None
                }
            }
        }, 
        "required":{
            "host":"dns.example.ru",
            "max_threads":10
        }, 
        "unrequired":{}
    },
    "gitlab":{
        "functions":{
            "get_namespace_owner":{
                "required":{},
                "functions":{
                    "query": execute_gitlab_namespace_owner_request,
                    #"converter": lambda: None
                }
            },
            "search":{
                "required":{},
                "functions":{
                    "query": execute_gitlab_search_request
                    #"converter": lambda: None
                }
            }
        }, 
        "required":{
            "url":"https://gitlab.example.ru",
            "timeout": 60,
            #"key":{"system":"foo", "account":"bar"},
            "max_threads":10
        }, 
        "unrequired":{}
    },
    "irp_iris":{
        "functions":{
            "get_all_alerts":{
                "required":{
                    "per_page":10000
                },
                "functions":{
                    "query": execute_function_iris_get_alerts,
                    #"converter": lambda: None
                }
            }
        }, 
        "required":{
            "url":"https://iris.example.ru",
            "timeout": 60,
            #"key":{"system":"foo", "account":"bar"},
            "max_threads":10
        },
        "unrequired":{}
    },
    "irp_thehive":{
        "functions":{
            "get_alerts":{
                "required":{
                    "filter":{},      # операция фильтра TheHive (dict). {} -> без фильтра
                    "limit":1000      # максимум алертов на страницу (page from=0..to=limit)
                },
                "unrequired":{
                    "sort":{"_fields":[{"_createdAt":"desc"}]},
                    "extra_data":[],
                    "flatten":False
                },
                "functions":{
                    "query": execute_thehive_get_alerts,
                    #"converter": lambda: None
                }
            }
        },
        "required":{
            "url":"https://thehive.example.ru",
            "timeout": 60,
            #"key":{"system":"foo", "account":"bar"},
            "max_threads":10
        },
        "unrequired":{
            "verify":False
        }
    },
    "teleport":{
        # "functions":{
        #     "get_hosts":{
        #         "required":{
        #             "ttl":600
        #         },
        #         "functions":{
        #             "query": execute_function_get_hosts_teleport,
        #             #"converter": lambda: None
        #         }
        #     }
        # }, 
        # "required":{
        #     "host":"teleport.example.ru",
        #     #"key":[{"system":"teleport", "account":"foo.bar"},{"system":"teleport", "account":"foo.bar_TOTP"}],
        #     "max_threads":10
        # }, 
        # "unrequired":{}
    },
    "youtrack":{
        "functions":{
            "search_in_project":{
                "required":{
                    "fields":[{"customFields":["name", {"value":"name"}]}, "summary"]
                },
                "functions":{
                    "query": execute_youtrack_project_finder,
                    #"converter": lambda: None
                }
            },
            "search_in_all_projects":{
                "required":{
                    "fields":[{"customFields":["name", {"value":"name"}]}, "summary"]
                },
                "functions":{
                    "query": execute_youtrack_all_project_issue_finder,
                    #"converter": lambda: None
                }
            },
            "search_in_all_articles":{
                "required":{
                    "fields":["idReadable", "summary"],
                    "fields_with_content":["idReadable", "summary", "content"]
                },
                "functions":{
                    "query": execute_youtrack_all_articles_finder,
                    #"converter": lambda: None
                }
            }
        }, 
        "required":{
            "url":"https://youtrack.example.ru",
            "timeout": 60,
            #"key":{"system":"foo", "account":"bar"},
            "max_threads":10
        }, 
        "unrequired":{}
    },
    "grafana":{
        # "functions":{
        #     "get_table":{
        #         "required":{
        #             "data_source_uid":{"9Md-vGvIo": "75"},
        #             "api_path": "/api/ds/query/",
        #             "datasource_type": "prometheus",
        #             "expr":'probe_success{job=\\"vm\\"}',
        #             #https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
        #             "server_timezone":"Europe/Moscow",
        #             "ttl":600
        #         },
        #         "functions":{
        #             "query": execute_grafana_export_table_requests,
        #             #"converter": lambda: None
        #         }
        #     }
        # }, 
        # "required":{
        #     "url":"https://grafana.example.ru",
        #     #"key":{"system":"foo", "account":"bar"},
        #     "max_threads":10
        # }, 
        # "unrequired":{}
    },
    "python_requests":{
        "functions":{
            "nane":{
                "required":{
                    "url":""
                }
            }
        }, 
        "required":{}, 
        "unrequired":{}
    },
    "pandas_im":{
        "functions":{
            "dynamic_aggr":{
                "required":{
                    "target_data":"vpn_data",
                    "list_to_str_dict":{'kibana.alert.rule.indices':'indices'},
                    "groupby_list":[
                        "host.hostname", #*
                        "decorations.computer_name",
                        "kibana.alert.rule.name", #*
                        "signal.rule.description",
                        "indices",
                        "process.command_line" #*
                    ],
                    "agg_dict":{
                        '@timestamp': ['min',"max","count"],
                        'signal.original_time': ['min',"max","count"],
                        'process.pid': ['min',"max","count"]
                    },
                    "dynamic_groupby_list":["host.hostname","kibana.alert.rule.name","process.command_line"],
                    "dynamica_agg_dict" :{
                        "@timestamp_min":"min",
                        "@timestamp_max":"max",
                        "@timestamp_count":"sum"
                    }
                },
                "functions":{
                    "query": execute_pandas_dynamic_aggregation,
                    #"converter": lambda: None
                }
            },
            "aggr":{
                "required":{
                    "target_data":"vpn_data",
                    "list_to_str_dict":{'kibana.alert.rule.indices':'indices'},
                    "groupby_list":[
                        "host.hostname", #*
                        "decorations.computer_name",
                        "kibana.alert.rule.name", #*
                        "signal.rule.description",
                        "indices",
                        "process.command_line" #*
                    ],
                    "agg_dict":{
                        '@timestamp': ['min',"max","count"],
                        'signal.original_time': ['min',"max","count"],
                        'process.pid': ['min',"max","count"]
                    }
                },
                "functions":{
                    "query": execute_pandas_aggregation,
                    #"converter": lambda: None
                }
            },
            "time_grouper_aggr":{
                "required":{
                    "target_data":"vpn_data",
                    "list_to_str_dict":{'kibana.alert.rule.indices':'indices'},
                    "groupby_list":[
                        "host.hostname", #*
                        "decorations.computer_name",
                        "kibana.alert.rule.name", #*
                        "signal.rule.description",
                        "indices",
                        "process.command_line" #*
                    ],
                    "agg_dict":{
                        '@timestamp': ['min',"max","count"],
                        'signal.original_time': ['min',"max","count"],
                        'process.pid': ['min',"max","count"]
                    },
                    "frequency":"",
                    "key":"",
                    "format":""
                },
                "functions":{
                    "query": execute_pandas_aggregation_with_time_grouper,
                    #"converter": lambda: None
                }
            },
            "shift":{
                "required":{
                    "target_data":"vpn_data",
                    "list_to_str_dict":{'kibana.alert.rule.indices':'indices'},
                    "groupby_list":[ # может быть пустым
                        "host.hostname", #*
                        "decorations.computer_name",
                        "kibana.alert.rule.name", #*
                        "signal.rule.description",
                        "indices",
                        "process.command_line" #*
                    ],
                    "target_column":"column",
                    "result_column":"shifted_column",
                    "shift":1,
                    "fill_value":""
                },
                "functions":{
                    "query": execute_pandas_shift,
                    #"converter": lambda: None
                }
            },
            "union":{
                "required":{
                    "target_data":["data_1", "data_2"]
                },
                "functions":{
                    "query": execute_pandas_union
                    #"converter": lambda: None
                }
            }
        }, 
        "required":{}, 
        "unrequired":{}
    },
    "ollama":{
        "functions":{
            "chat":{
                "required":{
                    "url":"https://localhost:11434/api/chat",
                    "model":"llama3.2",
                    "format":"",
                    "main_prompt":"",
                    "data_for_analysis":["data1", "data2"]
                },
                "functions":{
                    "query": execute_ollama_chat_query,
                    #"converter":lambda: None
                }
            }
        },
        "required":{
            #"key":{"system":"foo", "account":"bar"},
            "max_threads":10
        },
        "unrequired":{
            "verify_certs":False,
            "request_timeout":300
        }
    },
    "llama":{
        "functions":{
            "chat":{
                "required":{
                    "model_path":"/models/mixtral-8x7b-instruct-v0.1.Q4_K_M.gguf",
                    "context_length":16000,
                    "cpu_threads":32,
                    "gpu_layers":0,
                    "max_tokens":32000,
                    "stop":["</s>"],
                    "data_for_analysis":["data1", "data2"]
                },
                "functions":{
                    "query": execute_llama_chat_query,
                    #"converter":lambda: None
                }
            }
        },
        "required":{
            "max_threads":2
        },
        "unrequired":{
            "verify_certs":False,
            "request_timeout":300
        }
    },
    "universal_harvester":{
        "functions":{
            "local_scenario":{
                "required":{
                    "scenario_name":"[BB] Local scenario",
                    "result_data_name":"data_name",
                    "parameters":{"data1", "data2"}
                },
                "functions":{
                    "query": execute_local_scenario,
                    #"converter":lambda: None
                }
            }
        },
        "required":{
            "max_threads":999
        },
        "unrequired":{}
    }
    #"kaspersky_kata":{"functions":{}, "required":{}, "unrequired":{}},
}
def get_source_function(source_type, function_name, current_state:dict):
    if source_type not in ENGINE_SOURCES_AND_FUNCTIONS_MAP:
        error_message = f"there is not source type {source_type} in ENGINE_SOURCES_AND_FUNCTIONS_MAP"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), {}
    
    if function_name not in ENGINE_SOURCES_AND_FUNCTIONS_MAP[source_type]["functions"]:
        error_message = f"there is not function {function_name} in ENGINE_SOURCES_AND_FUNCTIONS_MAP->{source_type}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), {}
    
    function_parameters = ENGINE_SOURCES_AND_FUNCTIONS_MAP[source_type]["functions"][function_name]["required"]
    function_object = ENGINE_SOURCES_AND_FUNCTIONS_MAP[source_type]["functions"][function_name]["functions"]["query"]
    
    return True, "Ok", currentFuncName(), (function_parameters, function_object)

    

def get_variable_type(text:str, current_state:dict):
    # empty
    if len(text) == 0:
        return True, "empty", currentFuncName(), ("string", "")
    # string
    if text[0] == '"' and text[-1] == '"' or text[0] == "'" and text[-1] == "'":
        return True, "empty", currentFuncName(), ("string", text[1:-1])
    # bool true
    if text == "True" or text == "true":
        return True, "empty", currentFuncName(),("boolean", True)
    # bool false
    if text == "False" or text == "false":
        return True, "empty", currentFuncName(),("boolean", False)
    # integer
    if re.search(r"^\d+$", text):
        return True, "empty", currentFuncName(),("integer", int(text))
    # float
    if re.search(r"^\d+\.\d*$", text):
        return True, "empty", currentFuncName(),("float", float(text))
    # list
    if re.search(r"^\[.*\]$", text, flags=re.DOTALL):
        if json_validate(text):
            return True, "empty", currentFuncName(),("list", json.loads(text))
        else:
            return False, "incorrect json node", currentFuncName(),("list", [])
    # dict
    if re.search(r"^\{.*\}$", text, flags=re.DOTALL):
        if json_validate(text):
            return True, "empty", currentFuncName(),("dict", json.loads(text))
        else:
            return False, "incorrect json node", currentFuncName(),("dict", {})
        
    return False, "unknow data type", currentFuncName(),("string", "")
        


def split_top_level(text, separator=','):
    """Разбить text по separator только на верхнем уровне вложенности.
    Разделители внутри (), [], {} и внутри строк '...'/"..." игнорируются,
    что позволяет параметрам содержать запятые (списки/SQL) и символ '|' (напр. SQLite '||')."""
    parts, buf = [], []
    depth = 0
    quote = None
    prev = ''
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote and prev != '\\':
                quote = None
        elif ch in ('"', "'"):
            quote = ch
            buf.append(ch)
        elif ch in '([{':
            depth += 1
            buf.append(ch)
        elif ch in ')]}':
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == separator and depth == 0:
            parts.append(''.join(buf))
            buf = []
        else:
            buf.append(ch)
        prev = ch
    parts.append(''.join(buf))
    return parts


def command_parser(text:str, current_state:dict):
    """команда разбора текстовой команды на список выполняемых команд движка
    на вход поступает plain text скрипта, на выходе получаем list выполняемых
    команд со всеми указанными инструкциями
    DEF -- объявить переменную
    CALC -- применить функцию для получение из уже существующих новые переменные
    GET 
    """

    # 1. удаляем комментарии /* .* */ (нежадно + многострочно, чтобы не вырезать код между комментариями)
    text = re.sub(r"/\*.*?\*/", '', text, flags=re.DOTALL)

    # 2. каждая новая команда должна начинаться с | (split только на верхнем уровне:
    #    '|' внутри строк/скобок, напр. SQLite '||', не считается разделителем команд)
    result = split_top_level(text, '|')

    # 3. чистим лишние символы 
    lines = [x.strip("\n ") for x in result]

    # 4. выделяем команды
    commands = []
    for line in lines:
        match = re.search(r"^(\S+)\s+(.*)$", line, flags=re.DOTALL)
        if match:
            commands.append({"command":match.group(1), "line":match.group(2)})

    # 5. обработка логики каждой команды
    for i, command in enumerate(commands):
        command["parsed"] = True
        command["parsed_comment"] = "Ok"

        match command["command"]:
            case "DEF":
                # команда присваивания переменных, через неё реализуется ввод параметров
                # DEF 3.14 AS pi
                match = re.search(r"^(.+)\s+(as|AS|As|aS)\s+(\S+)\s*$", command["line"], flags=re.DOTALL)
                #elements = re.split(r'\s+as|AS|As|aS\s+', command["line"], flags=re.DOTALL)
                if match:
                    command["variable_name"] = match.group(3).strip(" ")
                    variable_body = match.group(1).strip(" ")
                else:
                    command["parsed"] = False
                    command["parsed_comment"] = f"AS name not found for {i} command"
                    continue

                get_variable_type_result = get_variable_type(variable_body, current_state)
                if get_variable_type_result[0] == False:
                    command["variable_type"] = "unknow"
                    command["parsed"] = False
                    command["parsed_comment"] = f"{get_variable_type_result[1]} for {i} command"
                command["variable_type"] = get_variable_type_result[3][0]
                command["variable_value"] = get_variable_type_result[3][1]
                continue

            case "GET":
                # ищем возможный apply
                match = re.search(r"^APPLY:([^\()]+)\(([^\)]+)\):(\[[^\]]*\])\s+", command["line"])
                if match:
                    command["apply"] = {}
                    command["apply"]["data"] = match.group(1).strip(" ")
                    command["apply"]["raw_columns"] = match.group(2).strip(" ")
                    command["apply"]["unique"] = match.group(3).strip(" ")
                    if json_validate(command["apply"]["unique"]) == False:
                        command["parsed"] = False
                        command["parsed_comment"] = f"incorrect unique for {i} command (apply)"
                    command["apply"]["unique"] = json.loads(command["apply"]["unique"])
                    apply_columns = split_top_level(command["apply"]["raw_columns"], ',')
                    command["apply"]["columns"] = []
                    for apply_column in apply_columns:
                        column_match = re.match(r'^(?P<col>.+?)\s+[Aa][Ss]\s+(?P<alias>\S+)\s*$', apply_column.strip())
                        if not column_match:
                            command["parsed"] = False
                            command["parsed_comment"] = f"AS name not found for {i} command (apply)"
                            continue
                        command["apply"]["columns"].append({"column":column_match.group("col").strip(" "), "as":column_match.group("alias").strip(" ")})
                    command["line"] = re.sub(r"^APPLY:([^\()]+)\(([^\)]+)\):(\[[^\]]*\])\s+", "", command["line"], count=0, flags=re.DOTALL)

                match = re.search(r"^(.+)\s+(as|AS|As|aS)\s+(\S+)\s*$", command["line"])
                #elements = re.split(r'\s+as|AS|As|aS\s+', command["line"])
                if match:
                    command["data_name"] = match.group(3).strip(" ")
                    command_body = match.group(1).strip(" ")
                else:
                    command["parsed"] = False
                    command["parsed_comment"] = f"incorrect command format (source:func(parameters) as x)"
                    continue

                match = re.search(r"^([^:]+):([^\(]+)\((.*)\)$", command_body)
                if match:
                    command["source"] = match.group(1).strip(" ")
                    command["function"] = match.group(2).strip(" ")
                    command["raw_parameters"] = match.group(3).strip(" ")
                else:
                    command["parsed"] = False
                    command["parsed_comment"] = f"incorrect command format (source:func(parameters) as x)"
                    continue

                # разбираем параметры (split только на верхнем уровне -> значения могут содержать запятые)
                parameters = split_top_level(command["raw_parameters"], ',')
                command["parameters"] = {}
                for parameter in parameters:
                    if parameter == "":
                        continue
                    splitted = re.split(r'=', parameter, maxsplit=1)
                    if len(splitted) !=2:
                        command["parsed"] = False
                        command["parsed_comment"] = f"incorrect parameter {parameter} in {parameters}"
                    else:
                        command["parameters"][splitted[0].strip(" ")] = splitted[1].strip(" ")

            case "CALC":
                # CALC foo + bar as foobar
                match = re.search(r"^\s*(\S+)\s*(\+|\*|-)\s*(\S+)\s+(as|AS|As|as)\s+(\S+)\s*$", command["line"])
                if match:
                    command["variable_name_1"] = match.group(1).strip(" ")
                    command["operation"] = match.group(2).strip(" ")
                    command["variable_name_2"] = match.group(3).strip(" ")
                    command["result_name"] = match.group(5).strip(" ")
                else:
                    command["parsed"] = False
                    command["parsed_comment"] = "not recognized"

            case "SAVE":
                print("Internal Server Error")
            case "SHOW":
                # SHOW VARIABLES
                # SHOW VAR {var_name}
                # SHOW DATALIST
                # SHOW TABLE {data_name}
                # SHOW PLOT {data_name} x {x_column} y {y_column}
                print("Internal Server Error")
            case "NOTIFY":
                # NOTIFY notify_object_name("notify_text https://harvester.ru/%(_execution_id_)s")
                match = re.search(r'^\s*(\S+)\("(.*)"\)$', command["line"])
                if match:
                    command["notifier"] = match.group(1).strip(" ")
                    command["message"] = match.group(2).strip(" ")
                    command["user"] = current_state["username"]
                else:
                    command["parsed"] = False
                    command["parsed_comment"] = "not recognized"
            case _:  # The wildcard pattern acts as a default case
                print("Unknown HTTP Code")

    return commands

def get_command_dependency(command, current_state):
    try:
        dependency = []
        if "apply" in command:
            dependency.append(command["apply"]["data"])

        if 'parameters' in command:
            if command["source_type"] in ["sqlite3_im", "duckdb_im"]:
                sql_dependency = []
                sql_with_statement = []
                query_string = json.dumps(command['parameters'])

                for sql_depend in re.findall(r"(FROM|JOIN)\s+([^\s;)]+)",query_string):
                    sql_dependency.append(sql_depend[1])
                for sql_with in re.findall(r"(WITH|\),)\s+([^\s;]+)\s+AS\s+\(",query_string):
                    sql_with_statement.append(sql_with[1])
                for sql_dependency_candidate in sql_dependency:
                    if sql_dependency_candidate not in sql_with_statement:
                        dependency.append(sql_dependency_candidate)
            
            if command["source_type"] == "pandas_im":
                if isinstance(command['parameters']["target_data"], list):
                    dependency = dependency + command['parameters']["target_data"]
                elif isinstance(command['parameters']["target_data"], str):
                    dependency.append(command['parameters']["target_data"])

            if command["source_type"] == "ollama":
                if isinstance(command['parameters']["target_data"], list):
                    dependency = dependency + command['parameters']["target_data"]
                elif isinstance(command['parameters']["target_data"], str):
                    dependency.append(command['parameters']["target_data"])

        return True, "OK", currentFuncName(), list(set(dependency))
    
    except BaseException as e:
        error_message = f"{currentFuncName()} fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), dependency
    
def process_injections(node, parameters:dict, current_state:dict):
    """Функция, которая позволяет подставить параметры в выполняемые объекты скрипта. Изначально она работала на %()s встроенной
    функции python, но это работает не совсем корректно. Таким образом сложно вставить integer в валидный json. Поэтому
    эта функция была переписана с возможностью вставлять string (s), integer (i), float (f), boolean (b), list (l), dict (d)."""

    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))

        node_string = json.dumps(node)

        for parameter in parameters.keys():
            value = parameters[parameter]
            regular_expression = fr'%\({parameter}\)[sifbldx]'
            parameter_positions = [m.start() for m in re.finditer(regular_expression, node_string)]

            next_change_shift = 0
            for position in parameter_positions:
                injection_position = position + next_change_shift
                injection_type_position = injection_position + 2 + len(parameter) + 1
                injection_type = node_string[injection_type_position]
                injection_end_position = injection_type_position + 1

                quotation_mark_shift = 0

                if node_string[injection_position-1] == '"' and node_string[injection_end_position] == '"':
                    quotation_mark_shift = 1

                if injection_type == "s": #string вставляется как есть без впопросов и проблем
                    node_string = node_string[:injection_position] + str(value) + node_string[injection_end_position:]
                    current_next_shift = len(value) - (injection_end_position - injection_position)
                    next_change_shift = next_change_shift + current_next_shift
                elif injection_type == "x": #x вставляет строку без кавычек, то есть это прямая инъекция, что может быть не совсем безопасно
                    node_string = node_string[:injection_position-quotation_mark_shift] + str(value) + node_string[injection_end_position+quotation_mark_shift:]
                    current_next_shift = len(value) - (injection_end_position - injection_position)
                    next_change_shift = next_change_shift + current_next_shift
                elif injection_type == "i":
                    input_value = f"{value}"
                    node_string = node_string[:injection_position-quotation_mark_shift] + input_value + node_string[injection_end_position+quotation_mark_shift:]
                    current_next_shift = len(input_value) - (injection_end_position - injection_position)
                    next_change_shift = next_change_shift + current_next_shift
                elif injection_type == "f":
                    input_value = "{0:0.9f}".format(value)
                    node_string = node_string[:injection_position-quotation_mark_shift] + input_value + node_string[injection_end_position+quotation_mark_shift:]
                    current_next_shift = len(input_value) - (injection_end_position - injection_position)
                    next_change_shift = next_change_shift + current_next_shift
                elif injection_type == "b":
                    if value:
                        node_string = node_string[:injection_position-quotation_mark_shift] + "true" + node_string[injection_end_position+quotation_mark_shift:]
                        current_next_shift = 4 - (injection_end_position - injection_position)
                    else:
                        node_string = node_string[:injection_position-quotation_mark_shift] + "false" + node_string[injection_end_position+quotation_mark_shift:]
                        current_next_shift = 5 - (injection_end_position - injection_position)
                    
                    next_change_shift = next_change_shift + current_next_shift
                elif injection_type == "l" or injection_type == "d":
                    injection_string = json.dumps(value)
                    node_string = node_string[:injection_position-quotation_mark_shift] + str(injection_string) + node_string[injection_end_position+quotation_mark_shift:]
                    current_next_shift = len(injection_string) - (injection_end_position - injection_position)
                    next_change_shift = next_change_shift + current_next_shift
                else:
                    error_message = f"undefined injection_type: {str(injection_type)}"
                    logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                    return False, error_message, currentFuncName(), {}

        if json_validate(node_string) == False:
            error_message = f"incorrect json injected_node after injections"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), {}
        
        injected_node = json.loads(node_string)
        logger_log(syslog.LOG_DEBUG, get_log_message("done", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), injected_node

    except BaseException as e:
        error_message = f"process_injections fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), {}
    
def run_command(command, data_map, current_state):
    result = command["function_object"](command["parameters"], command["source_object"]['json'], data_map, current_state)
    if not result[0]:
        error_message = f"{currentFuncName()} error: {result[1]}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), {}
    #data_map[command['data_name']] = result[3]
    return True, str(len(result[3])), currentFuncName(), result[3]

def run_apply_command(command, data_map, current_state):
    import pandas
    applyed_data = data_map[command['apply']['data']]
    if len(applyed_data) == 0:
        return True, "empty applyed data", currentFuncName(), []
    # проверяем, что применяемые столбцы есть в каждой записи данных
    for i, line in enumerate(applyed_data):
        for column in command['apply']['columns']:
            if column['column'] not in line:
                return False, f"there is not column {column['column']} in {i} line of {command['apply']['data']}", currentFuncName(), []
     # выполнение
    data = []
    for i, line in enumerate(applyed_data): 
        # выделяем параметры из данных
        variables = {}
        for column in command['apply']['columns']:
            variables[column["as"]] = line[column['column']]
        # инъектируем параметры
        variables2command_injection_result = process_injections(command["parameters"], variables, current_state)
        if variables2command_injection_result[0] == False:
            error_message = f"apply var injection error: {variables2command_injection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []
        current_parameters = variables2command_injection_result[3]

        shard_result = command["function_object"](current_parameters, command["source_object"]['json'], data_map, current_state)
        if not shard_result[0]:
            error_message = f"{currentFuncName()} {i} iteration error: {shard_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), {}
        # добавляем applied_ к данным
        for shard_line in shard_result[3]:
            for column in command['apply']['columns']:
                shard_line[f"applied_{column["as"]}"] = line[column['column']]

        data = data + shard_result[3]
    # дедубликация при необходимости
    if "unique" in command["apply"]:
        if len(command["apply"]["unique"]) > 0:
            data = pandas.DataFrame(data).drop_duplicates(command["apply"]["unique"]).to_dict('records')
    
    #data_map[command['data_name']] = data
    return True, str(len(data)), currentFuncName(), data


