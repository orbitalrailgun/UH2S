import syslog
import sqlite3
import base64
import json
from app.logging import get_log_message, logger_log, currentFuncName, currentTimestamp
from app.crptgrphy import decrypt, encrypt

def create_db_connection(current_state: dict):
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))

        #################################
        # раскрываем конфигурацию
        #################################
        # в current_state должен быть db_conf

        if "db_conf" not in current_state:
            error_message = f"db_conf not in current_state"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        
        if isinstance(current_state["db_conf"], str) == False:
            error_message = f"db_conf in current_state is not a string"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        
        decrypt_result = decrypt(current_state["db_conf"], current_state)
        if decrypt_result[0] == False:
            error_message = f"db_conf decrypting is false"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        
        db_conf = json.loads(base64.b64decode(decrypt_result[3].encode()).decode())

        if "type" not in db_conf:
            error_message = f"type not in db_conf"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        
        if db_conf["type"] == "sqlite3":
            if "sqlite3" not in db_conf:
                error_message = f"sqlite3 not in db_conf"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                return False, error_message, currentFuncName(), None
            
            if isinstance(db_conf["sqlite3"], dict) == False:
                error_message = f"db_conf.sqlite3 is not a dict"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                return False, error_message, currentFuncName(), None
            
            if "db_path" not in db_conf["sqlite3"]:
                error_message = f"db_path not in db_conf.sqlite3"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                return False, error_message, currentFuncName(), None

            connection = sqlite3.connect(db_conf["sqlite3"]["db_path"])
            logger_log(syslog.LOG_DEBUG, get_log_message("done", currentFuncName(), current_state))
            query_parameter_inputter = "?"
            return True, query_parameter_inputter, currentFuncName(), connection
        
        elif db_conf["type"] == "postgresql":
            if "postgresql" not in db_conf:
                error_message = f"postgresql not in db_conf"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                return False, error_message, currentFuncName(), None
            
            if isinstance(db_conf["postgresql"], dict) == False:
                error_message = f"db_conf.postgresql is not a dict"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                return False, error_message, currentFuncName(), None
            
            if "host" not in db_conf["postgresql"]:
                error_message = f"host not in db_conf.postgresql"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                return False, error_message, currentFuncName(), None
            
            if "port" not in db_conf["postgresql"]:
                error_message = f"port not in db_conf.postgresql"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                return False, error_message, currentFuncName(), None
            
            if "db_name" not in db_conf["postgresql"]:
                error_message = f"dbname not in db_conf.postgresql"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                return False, error_message, currentFuncName(), None
            
            if "login" not in db_conf["postgresql"]:
                error_message = f"login not in db_conf.postgresql"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                return False, error_message, currentFuncName(), None
            
            if "password" not in db_conf["postgresql"]:
                error_message = f"password not in db_conf.postgresql"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                return False, error_message, currentFuncName(), None
            import psycopg2
            connection = psycopg2.connect(
                host = db_conf["postgresql"]["host"], 
                port = db_conf["postgresql"]["port"],
                dbname = db_conf["postgresql"]["db_name"],
                user = db_conf["postgresql"]["login"],
                password = db_conf["postgresql"]["password"]
            )
            logger_log(syslog.LOG_DEBUG, get_log_message("done", currentFuncName(), current_state))
            query_parameter_inputter = "%s"
            return True, query_parameter_inputter, currentFuncName(), connection
        else:
            error_message = f"unsupported db type {db_conf["type"]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None

def db_init(current_state):
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))

        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        cursor = connection.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS access_networks (cidr TEXT, allow TEXT, comment TEXT);")
        cursor.execute("CREATE TABLE IF NOT EXISTS users (enabled BOOLEAN, name TEXT, pass TEXT, roles TEXT, json TEXT);")
        cursor.execute("CREATE TABLE IF NOT EXISTS secrets (system TEXT, account TEXT, secret TEXT, comment TEXT);")
        cursor.execute("CREATE TABLE IF NOT EXISTS objects (name TEXT, roles TEXT, version INTEGER, timestamp TEXT, type TEXT, owner TEXT, json TEXT);")
        cursor.execute("CREATE TABLE IF NOT EXISTS executions (id TEXT, owner TEXT, timestamp TEXT, status INTEGER, json TEXT);")
        cursor.execute("CREATE TABLE IF NOT EXISTS storage (id TEXT, owner TEXT, execution TEXT, json TEXT);")
        cursor.execute("CREATE TABLE IF NOT EXISTS settings (scope TEXT, key TEXT, value TEXT);")
        connection.commit()
        cursor.execute("INSERT INTO access_networks(cidr, allow, comment) SELECT '127.0.0.0/8', true, 'localhost' WHERE NOT EXISTS(SELECT * FROM access_networks);")
        cursor.execute("INSERT INTO users(enabled, name, pass, roles, json) SELECT true, 'harvester', '$2a$12$csKo6ccYS3Kjc3e2JAu4VucbzO9vTBlvdjxCoTOVAYSnli2EXll3q', '[\"fullmaster\"]', '{}' WHERE NOT EXISTS(SELECT * FROM users) AND NOT EXISTS(SELECT * FROM storage) AND NOT EXISTS(SELECT * FROM objects) AND NOT EXISTS(SELECT * FROM executions);")
        connection.commit()
        cursor.close()
        connection.close()
    except BaseException as e:
        logger_log(syslog.LOG_CRIT, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None
    
    logger_log(syslog.LOG_DEBUG,get_log_message("done", currentFuncName(), current_state))
    return True, "OK", currentFuncName(), None

def get_access_networks(current_state):
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        query = f"SELECT cidr, allow, comment FROM access_networks;"
        cursor = connection.cursor()
        cursor.execute(query)
        result = cursor.fetchall()
        cursor.close()
        connection.close()

        if not result:
            logger_log(syslog.LOG_ERR, get_log_message("object not found", currentFuncName(), current_state))
            return False, "object not found", currentFuncName(), []
        
        access_networks = []

        for network_line in result:
            columns = ["cidr", "allow", "comment"]
            selected_object = dict(zip(columns, network_line))
            access_networks.append(selected_object)

        return True, "Ok", currentFuncName(), access_networks

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None

def get_actual_object_by_name(name, type, current_state):
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        # query = "SELECT name, roles, version, type, owner, json FROM objects WHERE name LIKE %(inputter)s AND type IN %(inputter)s AND version = (SELECT MAX(version) FROM objects WHERE name LIKE %(inputter)s AND type IN %(inputter)s);"
        # db_inputter_modificator = {"inputter": create_db_connection_result[1]}
        # query = query % db_inputter_modificator
        query = f"SELECT name, roles, version, timestamp, type, owner, json FROM objects WHERE name LIKE '{name}' AND type IN {type} AND version = (SELECT MAX(version) FROM objects WHERE name LIKE '{name}' AND type IN {type});"
        cursor = connection.cursor()
        #cursor.execute(query, (name, type, name, type))
        cursor.execute(query)
        result = cursor.fetchone()
        cursor.close()
        connection.close()

        if not result:
            logger_log(syslog.LOG_ERR, get_log_message("object not found", currentFuncName(), current_state))
            return False, "object not found", currentFuncName(), []
        
        columns = ["name", "roles", "version", "timestamp", "type", "owner", "json"]
        selected_object = dict(zip(columns, result))
        selected_object["roles"] = json.loads(selected_object["roles"])
        selected_object["json"] = json.loads(selected_object["json"])

        return True, "Ok", currentFuncName(), selected_object

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None

def get_object_by_name_and_version(name, version, current_state):
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        query = f"SELECT name, roles, version, timestamp, type, owner, json FROM objects WHERE name LIKE '{name}' AND version = {version};"
        cursor = connection.cursor()
        cursor.execute(query)
        result = cursor.fetchone()
        cursor.close()
        connection.close()

        if not result:
            logger_log(syslog.LOG_ERR, get_log_message("object not found", currentFuncName(), current_state))
            return False, "object not found", currentFuncName(), []
        
        columns = ["name", "roles", "version", "timestamp", "type", "owner", "json"]
        selected_object = dict(zip(columns, result))
        selected_object["roles"] = json.loads(selected_object["roles"])
        selected_object["json"] = json.loads(selected_object["json"])

        return True, "Ok", currentFuncName(), selected_object

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None

def create_new_object_version(name, type, roles, json_object, current_state):
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        query = f"""INSERT INTO objects (name, roles, version, timestamp, type, owner, json) 
        VALUES ('{name}','{json.dumps(roles, indent=0, ensure_ascii=False)}', (SELECT MAX(version) FROM objects WHERE name='{name}') + 1, '{currentTimestamp()}', '{type}',
        '{current_state.get("username", "unknown")}', '{json.dumps(json_object, indent=0, ensure_ascii=False)}');"""
        cursor = connection.cursor()
        cursor.execute(query)
        cursor.close()
        connection.commit()
        connection.close()

        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None

def create_new_object(name, type, roles, json_object, current_state):
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        query = f"""INSERT INTO objects (name, roles, version, timestamp, type, owner, json) 
        VALUES ('{name}','{json.dumps(roles, indent=0, ensure_ascii=False)}', 1, '{currentTimestamp()}', '{type}',
        '{current_state.get("username", "unknown")}', '{json.dumps(json_object, indent=0, ensure_ascii=False)}');"""
        cursor = connection.cursor()
        cursor.execute(query)
        cursor.close()
        connection.commit()
        connection.close()

        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None
    
def get_all_object_versions(object_name, current_state):
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        query = f"""
            SELECT name, roles, version, timestamp, type, owner
            FROM objects
            WHERE name LIKE '{object_name}'
            ORDER BY version DESC;
        """
        cursor = connection.cursor()
        cursor.execute(query)
        result = cursor.fetchall()
        cursor.close()
        connection.close()

        if not result:
            logger_log(syslog.LOG_ERR, get_log_message("object not found", currentFuncName(), current_state))
            return False, "object not found", currentFuncName(), []
        
        objects = []

        for object_line in result:
            columns = ["name", "roles", "version", "timestamp", "type", "owner"]
            selected_object = dict(zip(columns, object_line))
            selected_object["roles"] = json.loads(selected_object["roles"])
            #selected_object["json"] = json.loads(selected_object["json"])
            objects.append(selected_object)

        return True, "Ok", currentFuncName(), objects

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None

def get_all_actual_objects(current_state):
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        query = """
            WITH actual_version AS 
            (
                SELECT DISTINCT name, max(version) as version
                FROM objects
                GROUP BY name
            )
            SELECT objects.name, objects.roles, objects.version, objects.timestamp, objects.type, objects.owner/*, objects.json */
            FROM objects JOIN actual_version ON objects.name = actual_version.name 
            WHERE objects.version = actual_version.version 
            ORDER BY  objects.type, objects.name;
        """
        cursor = connection.cursor()
        cursor.execute(query)
        result = cursor.fetchall()
        cursor.close()
        connection.close()

        if not result:
            logger_log(syslog.LOG_ERR, get_log_message("object not found", currentFuncName(), current_state))
            return False, "object not found", currentFuncName(), []
        
        actual_objects = []

        for object_line in result:
            columns = ["name", "roles", "version", "timestamp", "type", "owner"]
            selected_object = dict(zip(columns, object_line))
            selected_object["roles"] = json.loads(selected_object["roles"])
            #selected_object["json"] = json.loads(selected_object["json"])
            actual_objects.append(selected_object)

        return True, "Ok", currentFuncName(), actual_objects

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None

def db_get_secrets_list(current_state):
    query = "SELECT system, account, secret, comment FROM secrets;"
    
    logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        cursor = connection.cursor()
        cursor.execute(query)
        result = cursor.fetchall()
        cursor.close()
        connection.close()

        if result:
            columns = ["system", "account", "secret", "comment"]
            secrets_list = []
            for line in result:
                selected_object = dict(zip(columns, line))
                secrets_list.append(selected_object)

            logger_log(syslog.LOG_DEBUG, get_log_message("done", currentFuncName(), current_state))
            return True, "OK", currentFuncName(), secrets_list
        else:
            logger_log(syslog.LOG_ERR, get_log_message("db table is empty?", currentFuncName(), current_state))
            return False, "db table is empty?", currentFuncName(), None
    except BaseException as e:
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def update_secret_comment(system, account, comment, current_state):
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        query = f"""UPDATE secrets SET comment = '{comment}' WHERE system = '{system}' AND account = '{account}';"""
        cursor = connection.cursor()
        cursor.execute(query)
        cursor.close()
        connection.commit()
        connection.close()

        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None
    
def update_secret_secret_comment(system, account, comment, secret, current_state):
    try:
        #сначала шифруем секрет
        encrypt_result = encrypt(secret, current_state)
        if encrypt_result[0] == False:
            logger_log(syslog.LOG_ERR, get_log_message(encrypt_result[1], currentFuncName(), current_state))
            return False, f'cryptography encryption error for key {system}/{account}: {encrypt_result[1]}', currentFuncName(), None

        encrypted_secret = encrypt_result[3]

        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        query = f"""UPDATE secrets SET comment = '{comment}', secret = '{encrypted_secret}' WHERE system = '{system}' AND account = '{account}';"""
        cursor = connection.cursor()
        cursor.execute(query)
        cursor.close()
        connection.commit()
        connection.close()

        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None

def create_secret(system, account, comment, secret, current_state):
    try:
        #сначала шифруем секрет
        logger_log(syslog.LOG_INFO, get_log_message(f"Create secret start: {system}:{account}", currentFuncName(), current_state))
        encrypt_result = encrypt(secret, current_state)
        if encrypt_result[0] == False:
            logger_log(syslog.LOG_ERR, get_log_message(encrypt_result[1], currentFuncName(), current_state))
            return False, f'cryptography encryption error for key {system}/{account}: {encrypt_result[1]}', currentFuncName(), None

        encrypted_secret = encrypt_result[3]

        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        query = f"""INSERT INTO secrets (system, account, secret, comment) VALUES ('{system}','{account}','{encrypted_secret}','{comment}');"""
        cursor = connection.cursor()
        cursor.execute(query)
        cursor.close()
        connection.commit()
        connection.close()

        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None    

def delete_secret(system, account, current_state):
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        query = f"""DELETE FROM secrets WHERE system = '{system}' and account = '{account}';"""
        cursor = connection.cursor()
        cursor.execute(query)
        cursor.close()
        connection.commit()
        connection.close()

        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None 

def get_secret(system, account, current_state):
    logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        query = "SELECT system, account, secret, comment FROM secrets WHERE system LIKE %(inputter)s AND account LIKE %(inputter)s;"
        db_inputter_modificator = {"inputter": create_db_connection_result[1]}
        query = query % db_inputter_modificator

        cursor = connection.cursor()
        cursor.execute(query, (system, account,))
        result = cursor.fetchone()
        cursor.close()
        connection.close()

        

        if result:
            columns = ["system", "account", "secret", "comment"]
            selected_object = dict(zip(columns, result))
            # снимаем шифрование
            decrypt_result = decrypt(selected_object["secret"], current_state)
            if decrypt_result[0] == False:
                logger_log(syslog.LOG_ERR, get_log_message(decrypt_result[1], currentFuncName(), current_state))
                return False, f'cryptography decryption error for key {selected_object["system"]}/{selected_object["account"]}: {decrypt_result[1]}', currentFuncName(), None

            selected_object["value"] = decrypt_result[3]

            logger_log(syslog.LOG_DEBUG, get_log_message("done", currentFuncName(), current_state))
            return True, "OK", currentFuncName(), selected_object["value"]
        else:
            logger_log(syslog.LOG_ERR, get_log_message("key not found", currentFuncName(), current_state))
            return False, "key not found", currentFuncName(), None
    except BaseException as e:
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None
    
def get_source_threads_pool(current_state):
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        query = f"SELECT name, roles, version, type, owner, json FROM objects WHERE type LIKE 'source';"
        cursor = connection.cursor()
        #cursor.execute(query, (name, type, name, type))
        cursor.execute(query)
        result = cursor.fetchall()
        cursor.close()
        connection.close()

        if not result:
            logger_log(syslog.LOG_ERR, get_log_message("object not found", currentFuncName(), current_state))
            return False, "object not found", currentFuncName(), []
        
        columns = ["name", "roles", "version", "type", "owner", "json"]
        sources = [dict(zip(columns, line)) for line in result]

        source_thread_pool = {}

        for source in sources:
            source["json"] = json.loads(source["json"])
            if "threads_limit" in source["json"]:
                if isinstance(source["json"]["threads_limit"], int):
                    if source["json"]["threads_limit"] > 0:
                        source_thread_pool[source["name"]] = {"threads_limit":source["json"]["threads_limit"], "current":0}

        return True, "Ok", currentFuncName(), source_thread_pool

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None   

def get_user_by_username(username, current_state):
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            error_message = f"create_db_connection_result is false: {create_db_connection_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

        connection = create_db_connection_result[3]

        # query = "SELECT name, roles, version, type, owner, json FROM objects WHERE name LIKE %(inputter)s AND type IN %(inputter)s AND version = (SELECT MAX(version) FROM objects WHERE name LIKE %(inputter)s AND type IN %(inputter)s);"
        # db_inputter_modificator = {"inputter": create_db_connection_result[1]}
        # query = query % db_inputter_modificator
        query = f"SELECT enabled, name, pass, roles, json FROM users WHERE name LIKE '{username}';"
        cursor = connection.cursor()
        #cursor.execute(query, (name, type, name, type))
        cursor.execute(query)
        result = cursor.fetchone()
        cursor.close()
        connection.close()

        if not result:
            logger_log(syslog.LOG_ERR, get_log_message("user not found", currentFuncName(), current_state))
            return False, "object not found", currentFuncName(), []
        
        columns = ["enabled", "name", "pass", "roles", "json"]
        selected_user = dict(zip(columns, result))
        selected_user["roles"] = json.loads(selected_user["roles"])
        selected_user["json"] = json.loads(selected_user["json"])

        return True, "Ok", currentFuncName(), selected_user

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def set_user_password(username, password_hash, current_state):
    """Сменить пароль пользователя (UPDATE users.pass, параметризованно). password_hash — bcrypt-хэш (str)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"UPDATE users SET pass = {placeholder} WHERE name = {placeholder};", (password_hash, username))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def update_user_metadata(username, json_object, current_state):
    """Обновить метаданные пользователя (UPDATE users.json, параметризованно)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"UPDATE users SET json = {placeholder} WHERE name = {placeholder};",
                       (json.dumps(json_object, ensure_ascii=False), username))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def search_actual_objects(query, current_state, limit=50):
    """Поиск по содержимому актуальных версий объектов (LIKE по json). Возвращает
    name/type/roles(parsed)/json(raw) — фильтрация по ролям делается на уровне вызова."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        sql = f"""
            WITH actual_version AS (
                SELECT name, max(version) AS version FROM objects GROUP BY name
            )
            SELECT objects.name, objects.type, objects.roles, objects.json
            FROM objects JOIN actual_version
                ON objects.name = actual_version.name AND objects.version = actual_version.version
            WHERE objects.json LIKE {placeholder}
            ORDER BY objects.type, objects.name
            LIMIT {int(limit)};
        """
        cursor = connection.cursor()
        cursor.execute(sql, (f"%{query}%",))
        result = cursor.fetchall()
        cursor.close()
        connection.close()

        objects = []
        for line in (result or []):
            record = dict(zip(["name", "type", "roles", "json"], line))
            try:
                record["roles"] = json.loads(record["roles"]) if record["roles"] else []
            except BaseException:
                record["roles"] = []
            objects.append(record)
        return True, "Ok", currentFuncName(), objects

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def create_execution(execution_id, owner, status, json_object, current_state):
    """Записать запуск скрипта в таблицу executions (параметризованный INSERT — текст скрипта
    может содержать кавычки)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        query = f"INSERT INTO executions (id, owner, timestamp, status, json) VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder});"
        cursor = connection.cursor()
        cursor.execute(query, (execution_id, owner, currentTimestamp(), int(status), json.dumps(json_object, ensure_ascii=False)))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), execution_id

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def get_executions(owner, current_state, limit=500):
    """Список запусков (новые сверху). owner=None -> все владельцы (для fullmaster).
    Возвращает id, owner, timestamp, status, duration, script (из json — для отображения и поиска)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        if owner is None:
            query = f"SELECT id, owner, timestamp, status, json FROM executions ORDER BY timestamp DESC LIMIT {int(limit)};"
            cursor.execute(query)
        else:
            query = f"SELECT id, owner, timestamp, status, json FROM executions WHERE owner LIKE {placeholder} ORDER BY timestamp DESC LIMIT {int(limit)};"
            cursor.execute(query, (owner,))
        result = cursor.fetchall()
        cursor.close()
        connection.close()

        executions = []
        for line in (result or []):
            record = dict(zip(["id", "owner", "timestamp", "status", "json"], line))
            try:
                parsed = json.loads(record["json"]) if record["json"] else {}
            except BaseException:
                parsed = {}
            executions.append({
                "id": record["id"],
                "owner": record["owner"],
                "timestamp": record["timestamp"],
                "status": record["status"],
                "duration": parsed.get("duration_seconds"),
                "script": parsed.get("script", ""),
                "agent": bool(parsed.get("agent", False)),
            })
        return True, "Ok", currentFuncName(), executions

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def get_execution_by_id(execution_id, current_state):
    """Полная запись запуска по id (json распарсен)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        query = f"SELECT id, owner, timestamp, status, json FROM executions WHERE id LIKE {placeholder};"
        cursor = connection.cursor()
        cursor.execute(query, (execution_id,))
        result = cursor.fetchone()
        cursor.close()
        connection.close()

        if not result:
            return False, "execution not found", currentFuncName(), None

        columns = ["id", "owner", "timestamp", "status", "json"]
        execution = dict(zip(columns, result))
        execution["json"] = json.loads(execution["json"]) if execution["json"] else {}
        return True, "Ok", currentFuncName(), execution

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


# ───────────────────────── Хранилище настроек (settings) ─────────────────────────
# Таблица settings(scope, key, value): value хранится как JSON (любой тип).
# Конвенция scope: "global" — глобальные/админские; "user:<username>" — персональные.

SETTINGS_SCOPE_GLOBAL = "global"


def settings_user_scope(username):
    return f"user:{username}"


def get_setting(scope, key, default, current_state):
    """Получить одну настройку (JSON-декод). Не найдено -> (True, ..., default); ошибка -> (False, ..., default)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), default
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"SELECT value FROM settings WHERE scope = {placeholder} AND key = {placeholder};", (scope, key))
        row = cursor.fetchone()
        cursor.close()
        connection.close()

        if not row or row[0] is None:
            return True, "default", currentFuncName(), default
        return True, "Ok", currentFuncName(), json.loads(row[0])

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), default


def get_settings(scope, current_state):
    """Все настройки scope как dict {key: value}."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), {}
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"SELECT key, value FROM settings WHERE scope = {placeholder};", (scope,))
        rows = cursor.fetchall()
        cursor.close()
        connection.close()

        result = {}
        for key, value in (rows or []):
            try:
                result[key] = json.loads(value) if value is not None else None
            except BaseException:
                result[key] = value
        return True, "Ok", currentFuncName(), result

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), {}


def set_setting(scope, key, value, current_state):
    """Upsert настройки (UPDATE, иначе INSERT). value JSON-кодируется (параметризованно)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        value_json = json.dumps(value, ensure_ascii=False)
        cursor = connection.cursor()
        cursor.execute(f"UPDATE settings SET value = {placeholder} WHERE scope = {placeholder} AND key = {placeholder};",
                       (value_json, scope, key))
        if cursor.rowcount == 0:
            cursor.execute(f"INSERT INTO settings (scope, key, value) VALUES ({placeholder}, {placeholder}, {placeholder});",
                           (scope, key, value_json))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def delete_setting(scope, key, current_state):
    """Удалить настройку scope/key."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"DELETE FROM settings WHERE scope = {placeholder} AND key = {placeholder};", (scope, key))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None