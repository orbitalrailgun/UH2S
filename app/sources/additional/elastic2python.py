# функции преднозначены для опциональной генерации search_after на основе sort и query
def get_actual_search_after_from_query_and_sort(query, sort):
    # get sort field
    sort_field_stop_list = ["_doc"]
    if len(sort) < 1:
        return False, "get sort field error -> len(sort) < 1"
    sort_filed = ""
    order = "desc"
    for sort_node in sort:
        if isinstance(sort_node, dict) == False:
            return False, "get sort field error -> sort node is not a dict"
        sort_node_keys_list = list(sort_node.keys())
        if len(sort_node_keys_list) != 1:
            return False, "get sort field error -> len(sort_node_keys_list) != 1"
        sort_field_candidate = sort_node_keys_list[0]
        if sort_field_candidate in sort_field_stop_list:
            continue
        sort_filed = sort_field_candidate
        if "order" in sort_node[sort_field_candidate]:
            order = sort_node[sort_field_candidate]["order"]
        break
    if sort_filed == "":
        return False, "get sort field error -> there is not sort field candidate"
    
    def find_key_in_nested_dict(data, target_key):
        if isinstance(data, dict):
            if target_key in data:
                return True, data[target_key]  # Key found, return True and its value
            for key, value in data.items():
                found, result = find_key_in_nested_dict(value, target_key)
                if found:
                    return True, result
        elif isinstance(data, list):
            for item in data:
                found, result = find_key_in_nested_dict(item, target_key)
                if found:
                    return True, result
        return False, None  # Key not found
    # search search_after in query

    find_key_result = find_key_in_nested_dict(query, sort_filed)
    if find_key_result[0] == False:
        return False, "find field in query error -> there is not result"
    sort_field_query_node = find_key_result[1]
    if order == "desc":
        if "lte" in sort_field_query_node:
            return sort_field_query_node["lte"], "OK"
        if "lt" in sort_field_query_node:
            return sort_field_query_node["lt"], "OR"
        return False, "find field in query error -> there is not lte/lt field for desc sort"
    elif order == "asc":
        if "gte" in sort_field_query_node:
            return sort_field_query_node["gte"], "OR"
        if "gt" in sort_field_query_node:
            return sort_field_query_node["gt"], "OR"
        return False, "find field in query error -> there is not gte/gt field for asc sort"
    return False, "find field in query error -> unknown sort type"

# функция вытаскивает из ответа эластика (прямой запрос без агрегаций) данные в лист диктов
# работает только с полями fields
def get_data(input_):
    output = []
    if "hits" in input_:
        if "hits" in input_["hits"]:
            for data in input_['hits']['hits']:
                if 'fields' in data:
                    if isinstance(data['fields'], dict):
                        new_node = {}
                        new_node["_id"] = data['_id']
                        for key in data['fields']:
                            #key, "->", likes[key]
                            if isinstance(data['fields'][key], list):
                                if len(data['fields'][key]) == 1:
                                    new_node[key] = data['fields'][key][0]
                                    continue
                            new_node[key] = data['fields'][key]
                            
                        output.append(new_node)
    return output

# получение текущих позиций сортировки
def get_sort(input_):
    output = []
    if "hits" in input_:
        if "hits" in input_["hits"]:
            for data in input_['hits']['hits']:
                if 'sort' in data:
                    if isinstance(data['sort'], list):
                        new_node = {}
                        new_node["_id"] = data['_id']
                        new_node["sort"] = data['sort']
                        output.append(new_node)
    return output


# функция обработки бакета, является подфункцией buckets_proc
def bucket_proc(bucket):

    column_name = None
    data = {}
    next_bucket = None
    next_bucket_list = []
    if isinstance(bucket, dict):
        for key in bucket.keys():
            if isinstance(bucket[key], dict): # мы нашли следующий слой
                #next_bucket = key
                if "buckets" in bucket[key]:
                    next_bucket = key
                    next_bucket_list = bucket[key]["buckets"]
                else:
                    # Это тоже данные
                    if "value_as_string" in bucket[key]:
                        data[key] = bucket[key]["value_as_string"]
                    elif "value" in bucket[key]:
                        data[key] = bucket[key]["value"]
            elif key == "key": # имя столбца
                    column_name = bucket[key]
            else:
                 data[key] = bucket[key]
    return column_name, data, next_bucket, next_bucket_list
                    
# функция обработки бакетов, обрабатывает данные в агрегационных запросах
def buckets_proc(buckets_list, layer, output_dict, output_list):
    if isinstance(buckets_list, list):
        import copy
        for bucket in buckets_list:
            if layer == 0:
                output_dict = {"columns":{}}
            #print(bucket)   
            bucket_data = bucket_proc(bucket)
            #print(bucket_data)
            if bucket_data[2] is not None:
                output_dict["columns"][layer] = bucket_data[0]
                buckets_proc(bucket_data[3], layer+1, copy.deepcopy(output_dict), output_list)
            else:
                output_dict["columns"][layer] = bucket_data[0]
                buf = copy.deepcopy(output_dict)
                buf["data"] = bucket_data[1]                
                output_list.append(buf)
                
# функция преобразует отправляемый параметр aggs в заголовки итоговой таблички
def request_aggs_to_headers_proc(input_node, output_list, output_dict):
    import copy
    last_node_flag = True
    next_node_key = ""
    next_input_node_key = ""
    debug = False
    for agg_key in input_node.keys():
        node = input_node[agg_key]
        last_node_flag = True
        
        if debug: print(agg_key)
        for key in node.keys():
            if key != "aggs":
                node_value = node[key]
                for key_value in node_value:
                    if key_value == "field":
                        if debug: print(node_value[key_value] + "_" +key)
                        output_list.append(node_value[key_value] + "_" +key)
                        output_dict[agg_key] = node_value[key_value] + "_" +key
            else:
                last_node_flag = False
                next_node_key = key
                next_input_node_key = agg_key
    if last_node_flag == False:
        request_aggs_to_headers_proc(input_node[next_input_node_key][next_node_key], output_list, output_dict)        

# вытаскивает данные из ответа по агрегационному запросу
def get_data_aggs(input_, request_aggs): # можно доработать имена столбцов
    raw_output = []
    raw_node = input_
    if "rawResponse" in input_:
        raw_node = input_["rawResponse"]
    
    if "aggregations" in raw_node:
            aggr = raw_node["aggregations"]
            if isinstance(aggr, dict):
                key = list(aggr.keys())[0]
                layer = 0
                if "buckets" in aggr[key]:
                    buckets_proc(aggr[key]["buckets"], 0, {"columns":{}}, raw_output)    
    output = []

    #header_of_columns
    header_list = []
    header_dict = {}
    request_aggs_to_headers_proc(request_aggs,header_list,header_dict)

    # print(raw_output)
    # print(header_list)
    # print(header_dict)

    # тут много приседаний по предобразованию данных из ответа эластика в табличку
    # данные приходят в ужасно нелогичном виде, просто посмотри raw_output и header_list
    # там есть 2 блока, обрабатываемых по-разному
    # бакеты поиска эластика и метрики, они преобразуются в столбцы по-разному
    # бакеты обрабатываются с перезаписыванием
    for raw in raw_output:
        node = {}
        header_list_position = 0

        columns_buf = -1
        last_key = ""

        for i, columns_key in enumerate(raw["columns"].keys()):
            node[header_list[columns_key]] = raw["columns"][columns_key]


        for i, data_key in enumerate(raw["data"].keys()):
            if data_key in header_dict:
                node[header_dict[data_key]] = raw["data"][data_key]
            else:
                node[data_key] = raw["data"][data_key]
            
        output.append(node)
    return output

def data_taxi(elastic_client, index, query, sort, fields, size, search_after_shift, limit, debug = False):
    import pandas
    try:
        output_data = []
        debug_flag = debug
        if debug_flag:
            print("Получаем первичные данные")
        # проверка нулевого лимита
        if limit == 0:
            return True, f"zero limit", "data_taxi", []
        # сначала делаем первый запрос, получаем первый кусок данных
        try:
            response = elastic_client.search(
                index=index,
                body={
                            "query" : query,
                            "sort" : sort,
                            "fields" : fields,
                            "size" : size,
                             "_source":False
                        }
            )
            output_data = get_data(dict(response))
            current_sort = get_sort(dict(response))
        except BaseException as e:
            return False, f"elastic2python first query fail:{str(e)}", "data_taxi", []
        # проверяем первый полученный кусок данных, если данных столько, сколько указано в size
        # то скорее всего в запросе есть ещё, а значит надо сдвинуть поле search_after и повторить запрос
        if len(output_data) >= limit and limit > 0:
            return True, f"OK with limit", "data_taxi", output_data[:limit]
        
        if len(output_data) == size:
            sort_fields = [list(x.keys())[0] for x in sort] # по каким полям сортировка? парсим конструкцию sort
            taxi_step = 1
            while(True):
                if debug_flag:
                    print("Получаем данные итерационно, шаг", taxi_step)
                #search_after = [output_data[search_after_shift][sort_fields[0]]]
                search_after = current_sort[search_after_shift]["sort"]
                try:
                    response = elastic_client.search(
                        index=index, 
                        body={
                            "query" : query,
                            "sort" : sort,
                            "fields" : fields,
                            "size" : size,
                            "search_after":search_after,
                            "_source":False
                        }
                    )
                    new_data = get_data(dict(response))
                    current_sort = get_sort(dict(response))
                except BaseException as e:
                    return False, f"elastic2python iterate query fail:{str(e)}", "data_taxi", []

                output_data = output_data + new_data
                if debug_flag:
                    print("Получено данных", len(new_data))
                    print("Текущий search_after", str(search_after))

                if len(output_data) >= limit and limit > 0:
                    return True, f"OK with limit", "data_taxi", output_data[:limit]
                
                if len(new_data) != size:
                    break
                taxi_step = taxi_step + 1    
            
        result_data = pandas.DataFrame(output_data).drop_duplicates("_id").to_dict('records')
        return True, f"OK", "data_taxi", result_data
    
    except BaseException as e:
        return False, f"elastic2python fail:{str(e)}", "data_taxi", []


def data_taxi_aggs(elastic_client, index, query, aggs, debug = False, size = 0):
    if debug:
        print("Делаем запрос агрегации")
    # сначала делаем первый запрос, получаем первый кусок данных
    try:
        response = elastic_client.search(
                index=index, 
                body = {
                    "query":query, 
                    "size":size,
                    "aggs":aggs
                }
            )
        if debug:
            print("lib",response)
        output_data = get_data_aggs(dict(response), aggs)
        return True, "OK", "data_taxi_aggs", output_data
    except BaseException as e:
        return False, f"elastic2python aggs fail:{str(e)}", "data_taxi_aggs", []

def _hits_total(resp_json):
    """hits.total.value из ответа elastic (поддержка int и {value,relation})."""
    try:
        total = (resp_json or {}).get("hits", {}).get("total")
        if isinstance(total, dict):
            return total.get("value")
        return total
    except BaseException:
        return None


def _build_auth_header(auth_type, auth_user, secret):
    """Значение заголовка Authorization по типу аутентификации источника elastic_requests.
    api_key (по умолчанию) -> 'ApiKey <secret>'; basic_auth/http_auth -> 'Basic base64(user:secret)'.
    secret — это source['key']['value']; auth_user — source['key']['account'] (нужен для basic)."""
    at = (auth_type or "api_key").strip().lower()
    if at in ("api_key", "apikey"):
        return f"ApiKey {secret}"
    if at in ("basic_auth", "basic", "http_auth"):
        import base64
        token = base64.b64encode(f"{auth_user}:{secret}".encode()).decode()
        return f"Basic {token}"
    raise ValueError(f"unknown elastic auth_type: {auth_type}")


def data_taxi_requests(url, user_agent, api_key, verify_certs, timeout, query, sort, fields, size, search_after_shift, limit, debug = False,
                       max_retries=2, retry_backoff=0.5, retry_statuses=(429, 502, 503, 504), on_retry=None, debug_log=None,
                       auth_type="api_key", auth_user=None):
    import requests
    import pandas
    from app.sources.additional.retry import retry_call, RetryableError
    output_data = []
    matched_total = None  # hits.total (сколько совпало по фильтру) — для диагностики «почему 0»
    debug_flag = debug
    if debug_flag:
        print("Получаем первичные данные")

    if limit == 0:
        return True, f"zero limit", "data_taxi_requests", []

    headers = {
        'user-agent': user_agent,
        'content-type': 'application/json',
        "x-elastic-internal-origin": "kibana",
        "kbn-xsrf": "reporting",
        "Authorization": _build_auth_header(auth_type, auth_user, api_key),
    }
    # повторяем на сетевых ошибках/таймауте и транзиентных кодах (429/5xx); на 4xx — без повтора
    retryable = (RetryableError, requests.exceptions.ConnectionError, requests.exceptions.Timeout)

    def post(body):
        resp = requests.post(url, json=body, headers=headers, verify=verify_certs, timeout=timeout)
        if debug_flag:
            print(resp, resp.status_code, dict(resp.json()))
        if resp.status_code in retry_statuses:
            raise RetryableError(f"status {resp.status_code}", resp.status_code)
        # elastic может вернуть HTTP 200, но в теле {"error":..., "status": 4xx/5xx} — ловим это
        try:
            body_json = resp.json()
        except BaseException:
            body_json = None
        if isinstance(body_json, dict) and "error" in body_json:
            err = body_json.get("error")
            reason = err.get("reason") if isinstance(err, dict) else str(err)
            body_status = body_json.get("status")
            if isinstance(body_status, int) and (body_status == 429 or body_status >= 500):
                raise RetryableError(f"elastic body status {body_status}: {reason}", body_status)
            raise ValueError(f"elastic error (status {body_status}): {reason}")
        return resp

    # сначала делаем первый запрос, получаем первый кусок данных
    try:
        first_body = {"query": query, "size": size, "sort": sort, "fields": fields, "_source": False}
        response = retry_call(lambda: post(first_body), attempts=max_retries + 1,
                              backoff=retry_backoff, retryable_exceptions=retryable, on_retry=on_retry)
        if response.status_code not in [200, 201]:
            error_message = f"fail response code {response.status_code}: {response.text}"
            return False, error_message, "data_taxi_requests", None
        first_json = dict(response.json())
        matched_total = _hits_total(first_json)
        output_data = get_data(first_json)
        current_sort = get_sort(first_json)
    except BaseException as e:
        return False, f"elastic2python first query fail:{str(e)}", "data_taxi_requests", []
    # проверяем первый полученный кусок данных, если данных столько, сколько указано в size
    # то скорее всего в запросе есть ещё, а значит надо сдвинуть поле search_after и повторить запрос
    try:
        if len(output_data) >= limit and limit > 0:
            return True, f"OK with limit", "data_taxi_requests", output_data[:limit]

        if len(output_data) == size:
            taxi_step = 1
            while(True):
                if debug_flag:
                    print("Получаем данные итерационно, шаг", taxi_step)
                search_after = current_sort[search_after_shift]["sort"]
                try:
                    page_body = {"query": query, "size": size, "sort": sort, "fields": fields,
                                 "search_after": search_after, "_source": False}
                    response = retry_call(lambda: post(page_body), attempts=max_retries + 1,
                                          backoff=retry_backoff, retryable_exceptions=retryable, on_retry=on_retry)
                    if response.status_code not in [200, 201]:
                        error_message = f"fail response code {response.status_code}: {response.text}"
                        return False, error_message, "data_taxi_requests", None
                    new_data = get_data(dict(response.json()))
                    current_sort = get_sort(dict(response.json()))
                except BaseException as e:
                    return False, f"elastic2python iterate query fail:{str(e)}", "data_taxi", []

                output_data = output_data + new_data
                if debug_flag:
                    print("Получено данных", len(new_data))
                    print("Текущий search_after", str(search_after))

                if len(output_data) >= limit and limit > 0:
                    return True, f"OK with limit", "data_taxi_requests", output_data[:limit]

                if len(new_data) != size:
                    break
                taxi_step = taxi_step + 1

        if output_data:
            result_data = pandas.DataFrame(output_data).drop_duplicates("_id").to_dict('records')
        else:
            result_data = []

        # диагностика «почему 0»: matched (hits.total) vs rows (извлечено).
        # matched==0 -> фильтр ничего не нашёл; matched>0, rows==0 -> данные есть,
        # но поля из `fields` отсутствуют в документах (при _source:false) — проверьте fields.
        if not result_data and debug_log:
            try:
                debug_log({"matched": matched_total, "rows": 0,
                           "hint": ("filter matched nothing" if not matched_total
                                    else "matched but no values for requested `fields` (check fields/_source)"),
                           "response_sample": json.dumps(first_json, ensure_ascii=False)[:1500]})
            except BaseException:
                pass

        return True, f"OK (matched {matched_total}, rows {len(result_data)})", "data_taxi_requests", result_data
    except BaseException as e:
        return False, f"elastic2python query requests fail:{str(e)}", "data_taxi_requests", []

def data_taxi_aggs_requests(url, user_agent, api_key, verify_certs, timeout, query, aggs, debug = False, size = 0,
                            max_retries=2, retry_backoff=0.5, retry_statuses=(429, 502, 503, 504), on_retry=None,
                            auth_type="api_key", auth_user=None):
    import requests
    from app.sources.additional.retry import retry_call, RetryableError
    if debug:
        print("Делаем запрос агрегации")
    headers = {
        'user-agent': user_agent,
        'content-type': 'application/json',
        "x-elastic-internal-origin": "kibana",
        "kbn-xsrf": "reporting",
        "Authorization": _build_auth_header(auth_type, auth_user, api_key),
    }
    retryable = (RetryableError, requests.exceptions.ConnectionError, requests.exceptions.Timeout)

    def post():
        resp = requests.post(url, json={"query": query, "size": size, "aggs": aggs},
                             headers=headers, verify=verify_certs, timeout=timeout)
        if resp.status_code in retry_statuses:
            raise RetryableError(f"status {resp.status_code}", resp.status_code)
        # HTTP 200 с телом-ошибкой {"error":..., "status": 4xx/5xx}
        try:
            body_json = resp.json()
        except BaseException:
            body_json = None
        if isinstance(body_json, dict) and "error" in body_json:
            err = body_json.get("error")
            reason = err.get("reason") if isinstance(err, dict) else str(err)
            body_status = body_json.get("status")
            if isinstance(body_status, int) and (body_status == 429 or body_status >= 500):
                raise RetryableError(f"elastic body status {body_status}: {reason}", body_status)
            raise ValueError(f"elastic error (status {body_status}): {reason}")
        return resp

    try:
        response = retry_call(post, attempts=max_retries + 1, backoff=retry_backoff, retryable_exceptions=retryable, on_retry=on_retry)
        if debug:
            print("lib", response)
        if response.status_code not in [200, 201]:
            error_message = f"fail response code {response.status_code}: {response.text}"
            return False, error_message, "data_taxi_aggs_requests", None

        output_data = get_data_aggs(dict(response.json()), aggs)
        return True, "OK", "data_taxi_aggs_requests", output_data
    except BaseException as e:
        return False, f"elastic2python aggs requests fail:{str(e)}", "data_taxi_aggs_requests", []
    
def data_taxi_csv_downloader(elastic_client, index, query, sort, fields, size, search_after, search_after_shift, filename, writemode):
    import pandas # интерфейс для csv и как способ удалить дубликаты
    output_data = []
    debug_flag = True
    if debug_flag:
        print("Получаем первичные данные")
    # сначала делаем первый запрос, получаем первый кусок данных
    try:
        response = elastic_client.search(
            index=index,
            body={
                        "query" : query,
                        "sort" : sort,
                        "fields" : fields,
                        "size" : size,
                        "search_after":search_after
                    }

        )
        output_data = get_data(dict(response))
    except BaseException as e:
        print("Ошибка выполнения запроса (первичное получение данных)", e)
        return []
    # "append" -- значит каждую итерацию сразу записываем в файл и тем самым экономим ОЗУ. Потребуется в дальнейшем удалить дубликаты по полю _id.
    # "full" -- значит каждую итерацию пишем в ОЗУ. Дубликаты удалим через pandas сразу.
    if writemode == "append":
        pandas.DataFrame(output_data).to_csv(filename)
    # проверяем первый полученный кусок данных, если данных столько, сколько указано в size
    # то скорее всего в запросе есть ещё, а значит надо сдвинуть поле search_after и повторить запрос
    if len(output_data) == size:
        sort_fields = [list(x.keys())[0] for x in sort] # по каким полям сортировка? парсим конструкцию sort
        taxi_step = 1
        while(True):
            if debug_flag:
                print("Получаем данные итерационно, шаг", taxi_step)
                
            # блок обновления search_after 
            if writemode == "append" and taxi_step > 1:
                search_after = [new_data[search_after_shift][sort_fields[0]]]
            else:
                search_after = [output_data[search_after_shift][sort_fields[0]]]

            # блок запроса
            try:
                response = elastic_client.search(
                    index=index,
                    body={
                                "query" : query,
                                "sort" : sort,
                                "fields" : fields,
                                "size" : size,
                                "search_after":search_after
                            }
                )
                new_data = get_data(dict(response))
            except BaseException as e:
                print("Ошибка выполнения запроса (итеративное получение данных)", e)
                return []

            if writemode == "append":
                pandas.DataFrame(new_data).to_csv(filename, mode='a', header=False)
                #output_data = output_data + new_data
            else:
                output_data = output_data + new_data
            if debug_flag:
                print("Получено данных", len(new_data))
                print("Текущий search_after", str(search_after))
            if len(new_data) != size:
                break
            taxi_step = taxi_step + 1    
    if writemode == "full":
        pandas.DataFrame(output_data).drop_duplicates(["_id"]).to_csv(filename)
    if debug_flag:
        print("Получение данных завершено")

