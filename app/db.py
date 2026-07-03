import syslog
import sqlite3
import base64
import json
import time
import datetime
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
        cursor.execute("CREATE TABLE IF NOT EXISTS ai_log (timestamp TEXT, username TEXT, model TEXT, provider TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER, duration_ms INTEGER, ok INTEGER);")
        cursor.execute("CREATE TABLE IF NOT EXISTS api_keys (key_hash TEXT, owner TEXT, comment TEXT, enabled BOOLEAN, created_at TEXT, created_by TEXT, expires_at TEXT);")
        cursor.execute("CREATE TABLE IF NOT EXISTS schedules (id TEXT, name TEXT, owner TEXT, script_name TEXT, cron TEXT, enabled BOOLEAN, last_run TEXT, last_status INTEGER, created_at TEXT, created_by TEXT, json TEXT);")
        connection.commit()
        # лёгкая миграция: дочиняем недостающие колонки api_keys в уже существующих БД
        for column_def in ("created_at TEXT", "created_by TEXT", "expires_at TEXT"):
            try:
                cursor.execute(f"ALTER TABLE api_keys ADD COLUMN {column_def};")
                connection.commit()
            except BaseException:
                try:
                    connection.rollback()
                except BaseException:
                    pass
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


def create_access_network(cidr, allow, comment, current_state):
    """Добавить запись в access_networks (параметризованно). allow=True -> разрешающая сеть."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(
            f"INSERT INTO access_networks (cidr, allow, comment) VALUES ({placeholder}, {placeholder}, {placeholder});",
            (cidr, bool(allow), comment),
        )
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def delete_access_network(cidr, comment, current_state):
    """Удалить запись access_networks по cidr+comment (точное совпадение)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"DELETE FROM access_networks WHERE cidr = {placeholder} AND comment = {placeholder};", (cidr, comment))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None

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
        # смена пароля инвалидирует все активные сессии этого пользователя (self или админ)
        bump_user_session_epoch(username, current_state)
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def get_user_session_epoch(username, current_state):
    """Текущий session-epoch пользователя (токен инвалидации сессий). Хранится в settings(scope='session_epoch')."""
    return get_setting("session_epoch", username, "", current_state)


def bump_user_session_epoch(username, current_state):
    """Сменить session-epoch пользователя — это «отзывает» все его активные сессии."""
    import uuid
    return set_setting("session_epoch", username, uuid.uuid4().hex, current_state)


def set_user_enabled(username, enabled, current_state):
    """Заблокировать/разблокировать УЗ (UPDATE users.enabled). Блокировка отзывает сессии пользователя."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"UPDATE users SET enabled = {placeholder} WHERE name = {placeholder};", (bool(enabled), username))
        connection.commit()
        cursor.close()
        connection.close()
        bump_user_session_epoch(username, current_state)
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


def list_users(current_state):
    """Список пользователей (без хэша пароля): enabled/name/roles(parsed)/json(parsed). Для админ-панели."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]

        cursor = connection.cursor()
        cursor.execute("SELECT enabled, name, roles, json FROM users ORDER BY name;")
        rows = cursor.fetchall()
        cursor.close()
        connection.close()

        users = []
        for enabled, name, roles, meta in (rows or []):
            try:
                roles_parsed = json.loads(roles) if roles else []
            except BaseException:
                roles_parsed = []
            try:
                meta_parsed = json.loads(meta) if meta else {}
            except BaseException:
                meta_parsed = {}
            users.append({"enabled": bool(enabled), "name": name, "roles": roles_parsed, "json": meta_parsed})
        return True, "Ok", currentFuncName(), users

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def create_user(username, password_hash, roles, json_object, current_state):
    """Создать пользователя (enabled=true). Возвращает ошибку, если пользователь уже существует."""
    try:
        exists = get_user_by_username(username, current_state)
        if exists[0]:
            return False, f"пользователь '{username}' уже существует", currentFuncName(), None

        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(
            f"INSERT INTO users (enabled, name, pass, roles, json) VALUES (true, {placeholder}, {placeholder}, {placeholder}, {placeholder});",
            (username, password_hash, json.dumps(roles, ensure_ascii=False), json.dumps(json_object, ensure_ascii=False)),
        )
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def set_user_roles(username, roles, current_state):
    """Обновить роли пользователя (UPDATE users.roles, параметризованно)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"UPDATE users SET roles = {placeholder} WHERE name = {placeholder};",
                       (json.dumps(roles, ensure_ascii=False), username))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def create_ai_log_entry(username, model, provider, prompt_tokens, completion_tokens, duration_ms, ok, current_state):
    """Записать запрос к LLM в журнал ai_log (параметризованно). Время ставится сервером."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        total_tokens = prompt_tokens + completion_tokens
        cursor = connection.cursor()
        cursor.execute(
            f"INSERT INTO ai_log (timestamp, username, model, provider, prompt_tokens, completion_tokens, total_tokens, duration_ms, ok) "
            f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder});",
            (currentTimestamp(), username, model, provider, prompt_tokens, completion_tokens, total_tokens, int(duration_ms or 0), 1 if ok else 0),
        )
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def get_ai_log(current_state, limit=2000):
    """Журнал AI-запросов (последние limit), новые сверху."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]

        cursor = connection.cursor()
        cursor.execute(
            f"SELECT timestamp, username, model, provider, prompt_tokens, completion_tokens, total_tokens, duration_ms, ok "
            f"FROM ai_log ORDER BY timestamp DESC LIMIT {int(limit)};"
        )
        rows = cursor.fetchall()
        cursor.close()
        connection.close()

        columns = ["timestamp", "username", "model", "provider", "prompt_tokens", "completion_tokens", "total_tokens", "duration_ms", "ok"]
        entries = [dict(zip(columns, row)) for row in (rows or [])]
        return True, "Ok", currentFuncName(), entries

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def _hash_api_key(token):
    import hashlib
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def create_api_key(owner, comment, created_by, ttl_days, current_state):
    """Создать API-ключ для владельца (enabled=true). Возвращает токен в payload — показывается один раз.
    В БД хранится только sha256-хэш токена. ttl_days: число дней жизни (None/0 -> бессрочный)."""
    try:
        import secrets
        token = "uh_" + secrets.token_hex(32)
        key_hash = _hash_api_key(token)

        created_at = currentTimestamp()
        expires_at = ""
        try:
            if ttl_days and int(ttl_days) > 0:
                from datetime import datetime, timedelta
                expires_at = (datetime.fromisoformat(created_at) + timedelta(days=int(ttl_days))).isoformat()
        except BaseException:
            expires_at = ""

        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(
            f"INSERT INTO api_keys (key_hash, owner, comment, enabled, created_at, created_by, expires_at) "
            f"VALUES ({placeholder}, {placeholder}, {placeholder}, true, {placeholder}, {placeholder}, {placeholder});",
            (key_hash, owner, comment, created_at, created_by, expires_at),
        )
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), token

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def list_api_keys(current_state):
    """Список API-ключей (без самого токена): key_hash/owner/comment/enabled/created_at/created_by/expires_at."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]

        cursor = connection.cursor()
        cursor.execute("SELECT key_hash, owner, comment, enabled, created_at, created_by, expires_at FROM api_keys ORDER BY owner;")
        rows = cursor.fetchall()
        cursor.close()
        connection.close()

        columns = ["key_hash", "owner", "comment", "enabled", "created_at", "created_by", "expires_at"]
        keys = [dict(zip(columns, row)) for row in (rows or [])]
        return True, "Ok", currentFuncName(), keys

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def set_api_key_enabled(key_hash, enabled, current_state):
    """Включить/отключить API-ключ по его sha256-хэшу."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"UPDATE api_keys SET enabled = {placeholder} WHERE key_hash = {placeholder};", (bool(enabled), key_hash))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def delete_api_key(key_hash, current_state):
    """Удалить API-ключ по его sha256-хэшу."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"DELETE FROM api_keys WHERE key_hash = {placeholder};", (key_hash,))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def verify_api_key(token, current_state):
    """Проверить API-ключ. Возвращает (True, ..., owner) если ключ активен и владелец не заблокирован."""
    try:
        key_hash = _hash_api_key(token)
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"SELECT owner, enabled, expires_at FROM api_keys WHERE key_hash = {placeholder};", (key_hash,))
        row = cursor.fetchone()
        cursor.close()
        connection.close()

        if not row:
            return False, "invalid api key", currentFuncName(), None
        owner, enabled, expires_at = row[0], row[1], (row[2] if len(row) > 2 else "")
        if not enabled:
            return False, "api key disabled", currentFuncName(), None
        # срок жизни истёк? (ISO 8601 UTC сравним лексикографически)
        if expires_at and currentTimestamp() >= expires_at:
            return False, "api key expired", currentFuncName(), None
        # владелец не должен быть заблокирован
        owner_result = get_user_by_username(owner, current_state)
        if not owner_result[0] or not owner_result[3].get("enabled", False):
            return False, "api key owner disabled or missing", currentFuncName(), None
        return True, "Ok", currentFuncName(), owner

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


# ────────────────────────────────────────────────────────────────────────────
# storage — персистентный кэш данных DSL (SAVE→storage / LOAD / GET LOAD).
# Кэш ОБЩИЙ: ключ (id) глобальный, фильтр только по id; owner пишется для аудита.
# Данные хранятся JSON-конвертом в колонке json: {"created_ts", "ttl", "data"}.
# ────────────────────────────────────────────────────────────────────────────
def storage_save(key, records, ttl, current_state):
    """Сохранить таблицу (list-of-dicts) в storage под ключом key. Перезапись = DELETE+INSERT
    (одна транзакция; работает и в sqlite3, и в postgresql). ttl=None -> не истекает."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        now = int(time.time())
        owner = current_state.get("username", "")
        execution = current_state.get("main_session_id", "")

        cursor = connection.cursor()
        # сохраняем исходное время создания при перезаписи (updated_ts обновляем)
        created_ts = now
        cursor.execute(f"SELECT json FROM storage WHERE id = {placeholder};", (key,))
        existing = cursor.fetchone()
        if existing and existing[0]:
            try:
                prev = json.loads(existing[0])
                if isinstance(prev, dict) and prev.get("created_ts") is not None:
                    created_ts = int(prev["created_ts"])
            except BaseException:
                pass

        envelope = {"created_ts": created_ts,
                    "updated_ts": now,
                    "ttl": (int(ttl) if ttl is not None else None),
                    "data": records}

        cursor.execute(f"DELETE FROM storage WHERE id = {placeholder};", (key,))
        cursor.execute(
            f"INSERT INTO storage (id, owner, execution, json) VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder});",
            (key, owner, execution, json.dumps(envelope, ensure_ascii=False)))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), key

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def storage_load(key, current_state):
    """Прочитать конверт из storage по ключу. Возврат (ok, msg, func, envelope|None):
    отсутствие ключа -> (True, "Ok", .., None); битый/некорректный json -> (False, ..)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"SELECT json FROM storage WHERE id = {placeholder};", (key,))
        row = cursor.fetchone()
        cursor.close()
        connection.close()

        if not row:
            return True, "Ok", currentFuncName(), None
        try:
            envelope = json.loads(row[0]) if row[0] else None
        except BaseException:
            return False, f"storage '{key}' corrupt json envelope", currentFuncName(), None
        if not isinstance(envelope, dict) or "data" not in envelope:
            return False, f"storage '{key}' invalid envelope shape", currentFuncName(), None
        return True, "Ok", currentFuncName(), envelope

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def storage_delete(key, current_state):
    """Удалить строку storage по ключу. Идемпотентно (удаление отсутствующего ключа — ок)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]

        cursor = connection.cursor()
        cursor.execute(f"DELETE FROM storage WHERE id = {placeholder};", (key,))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), key

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def _iso_utc(ts):
    """Unix-время -> ISO 8601 UTC (для показа); None/битое -> ''."""
    try:
        return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).isoformat(timespec="seconds")
    except BaseException:
        return ""


def storage_list(current_state):
    """Список всех записей storage с метаданными (кэш общий). Возврат (ok, msg, func, list),
    где элемент: {id, owner, created_ts, updated_ts, ttl, rows, size_bytes, expired}
    (created_ts/updated_ts — ISO-строки UTC). Битые конверты не роняют список."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]

        cursor = connection.cursor()
        cursor.execute("SELECT id, owner, json FROM storage;")
        rows = cursor.fetchall()
        cursor.close()
        connection.close()

        now = int(time.time())
        entries = []
        for row in (rows or []):
            key, owner, raw = row[0], row[1], row[2]
            created = updated = None
            ttl = None
            record_count = 0
            size_bytes = len(raw) if raw else 0
            expired = False
            try:
                envelope = json.loads(raw) if raw else {}
                if isinstance(envelope, dict):
                    created = envelope.get("created_ts")
                    updated = envelope.get("updated_ts", created)   # старые конверты без updated_ts
                    ttl = envelope.get("ttl")
                    data = envelope.get("data")
                    record_count = len(data) if isinstance(data, list) else 0
                    if ttl is not None and created is not None and (now - int(created)) > int(ttl):
                        expired = True
            except BaseException:
                pass
            entries.append({
                "id": key,
                "owner": owner or "",
                "created_ts": _iso_utc(created),
                "updated_ts": _iso_utc(updated),
                "ttl": ("" if ttl is None else int(ttl)),
                "rows": record_count,
                "size_bytes": size_bytes,
                "expired": expired,
            })
        entries.sort(key=lambda e: e["updated_ts"], reverse=True)
        return True, "Ok", currentFuncName(), entries

    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


# ────────────────────────────────────────────────────────────────────────────
# schedules — расписания запуска сохранённых script-объектов по cron.
# ────────────────────────────────────────────────────────────────────────────
def create_schedule(schedule_id, name, owner, script_name, cron, enabled, created_by, current_state):
    """Создать расписание. Возврат (ok, msg, func, schedule_id)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]
        cursor = connection.cursor()
        cursor.execute(
            f"INSERT INTO schedules (id, name, owner, script_name, cron, enabled, last_run, last_status, created_at, created_by, json) "
            f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder});",
            (schedule_id, name, owner, script_name, cron, bool(enabled), "", None, currentTimestamp(), created_by, "{}"))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), schedule_id
    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def _schedule_rows(rows):
    columns = ["id", "name", "owner", "script_name", "cron", "enabled", "last_run", "last_status", "created_at", "created_by", "json"]
    return [dict(zip(columns, row)) for row in (rows or [])]


def list_schedules(current_state, owner=None):
    """Список расписаний (owner=None -> все, для админа). Возврат (ok, msg, func, list)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]
        cursor = connection.cursor()
        base = "SELECT id, name, owner, script_name, cron, enabled, last_run, last_status, created_at, created_by, json FROM schedules"
        if owner is None:
            cursor.execute(base + " ORDER BY name;")
            rows = cursor.fetchall()
        else:
            cursor.execute(base + f" WHERE owner = {placeholder} ORDER BY name;", (owner,))
            rows = cursor.fetchall()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), _schedule_rows(rows)
    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def get_schedule(schedule_id, current_state):
    """Одно расписание по id. Возврат (ok, msg, func, dict|None)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]
        cursor = connection.cursor()
        cursor.execute(
            f"SELECT id, name, owner, script_name, cron, enabled, last_run, last_status, created_at, created_by, json FROM schedules WHERE id = {placeholder};",
            (schedule_id,))
        row = cursor.fetchone()
        cursor.close()
        connection.close()
        rows = _schedule_rows([row]) if row else []
        return True, "Ok", currentFuncName(), (rows[0] if rows else None)
    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def update_schedule(schedule_id, name, script_name, cron, enabled, current_state):
    """Обновить поля расписания. Возврат (ok, msg, func, schedule_id)."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]
        cursor = connection.cursor()
        cursor.execute(
            f"UPDATE schedules SET name = {placeholder}, script_name = {placeholder}, cron = {placeholder}, enabled = {placeholder} WHERE id = {placeholder};",
            (name, script_name, cron, bool(enabled), schedule_id))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), schedule_id
    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def set_schedule_enabled(schedule_id, enabled, current_state):
    """Включить/выключить расписание."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]
        cursor = connection.cursor()
        cursor.execute(f"UPDATE schedules SET enabled = {placeholder} WHERE id = {placeholder};", (bool(enabled), schedule_id))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None
    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def delete_schedule(schedule_id, current_state):
    """Удалить расписание по id."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]
        cursor = connection.cursor()
        cursor.execute(f"DELETE FROM schedules WHERE id = {placeholder};", (schedule_id,))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None
    except BaseException as e:
        if 'connection' in locals():
            connection.close()
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


def set_schedule_last_run(schedule_id, timestamp, status, current_state):
    """Отметить время и статус последнего запуска расписания."""
    try:
        create_db_connection_result = create_db_connection(current_state)
        if create_db_connection_result[0] == False:
            return False, create_db_connection_result[1], currentFuncName(), None
        connection = create_db_connection_result[3]
        placeholder = create_db_connection_result[1]
        cursor = connection.cursor()
        cursor.execute(f"UPDATE schedules SET last_run = {placeholder}, last_status = {placeholder} WHERE id = {placeholder};",
                       (timestamp, (None if status is None else int(status)), schedule_id))
        connection.commit()
        cursor.close()
        connection.close()
        return True, "Ok", currentFuncName(), None
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