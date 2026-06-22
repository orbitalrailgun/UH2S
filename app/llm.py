def simple_chat(message, history, llm_object, current_state):
    """
    Этап 1
    Выделяем смысловую нагрузку запроса, выделяем маркеры поиска данных (о чём вопрос)
    
    Этап 2
    Согласно маркерам/тегам поиска обращаемся к долговременной памяти и выделяем графовый контекст
    
    Этап 3
    Добавляем графовый контекст к исходному запросу, добавляем список возможных обогащений по типам данных и контексту
    
    Этап 4 
    Делаем обогащения, повторяем запрос с дополнительным контекстом

    Этап 5
    Формируем окончательный ответ

    Этап 6
    Записываем в долговременную память факт и суть запроса

    Этап 7
    Записываем в долговременную память факт и суть ответа
    """
    pass

def mem_load_knowledge(knowledge_content, llm_object, current_state):
    pass

def mem_load_knowledge_attachment(knowledge_content, llm_object, current_state):
    pass

def get_raw_context_from_message(message, llm_object, current_state):
    """Функция из любых входных данных выделяет контекстные теги, 
    которые в дальнейшем будут применяться для поиска данных в долговременной памяти"""

    system_prompt = """You're an expert linguistic and data assistant tasked with extracting key searchable tags from any given text or data. These tags should represent important entities, events, locations, processes, or attributes that characterize the content of the source material. Your goal is to accurately identify types of subjects, objects, actions, and attributes necessary for effective information retrieval.

    Your main tasks are:

        Extracting Subjects: Proper names, organizations, people, items.
        Identifying Objects: Specific things, concepts, places.
        Pulling Actions: Verbs representing core processes or state changes.
        Formatting the extracted tags into a JSON array:

    ["Subject1", "Object1", "Action1", "Attribute1"...]

    Example 1:Input Text: Katya bought a new phone at the electronics store.Search Tags: ["Katya", "Phone", "Electronics Store", "Bought"]

    Example 2:Input Text: A St. Petersburg company released a new book by a famous writer.Search Tags: ["St. Petersburg Company", "Book", "Writer", "Released"]

    Make sure your tags are concise, clear, and reflect the essence of the provided text."""


