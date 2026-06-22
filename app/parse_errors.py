import syslog
from app.logging import get_log_message, logger_log, currentFuncName


def get_parse_errors(parsed_script: dict, current_state: dict):
    """
    Extract and format parse errors from a parsed script.
    
    Args:
        parsed_script (dict): The parsed script containing commands with parse status
        current_state (dict): Current application state for logging context
        
    Returns:
        tuple: (success: bool, message: str, function_name: str, errors_text: str)
               - success: True if function executed successfully, False if exception occurred
               - message: Status message ("clear" if no errors, "not clear" if errors found)
               - function_name: Name of the current function for logging
               - errors_text: Concatenated error messages or empty string if no errors
    """
    try:
        # Extract error messages from unparsed commands using list comprehension
        error_messages = [
            command["parsed_comment"] 
            for command in parsed_script 
            if not command.get("parsed", True)  # Default to True if "parsed" key doesn't exist
        ]
        
        # Join error messages with newlines
        errors_text = "\n".join(error_messages)
        
        # Determine status based on whether errors were found
        if errors_text:
            return True, "not clear", currentFuncName(), errors_text
        else:
            return True, "clear", currentFuncName(), errors_text
            
    except Exception as e:
        error_message = f"{currentFuncName()} error: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), ""


def get_parse_errors_enhanced(parsed_script: dict, current_state: dict):
    """
    Enhanced version of get_parse_errors with additional features:
    - Error categorization
    - Line number tracking
    - Error severity levels
    
    Args:
        parsed_script (dict): The parsed script containing commands with parse status
        current_state (dict): Current application state for logging context
        
    Returns:
        tuple: (success: bool, message: str, function_name: str, error_details: dict)
               - success: True if function executed successfully, False if exception occurred
               - message: Status message ("clear" if no errors, "not clear" if errors found)
               - function_name: Name of the current function for logging
               - error_details: Dictionary containing categorized error information
    """
    try:
        error_details = {
            "total_errors": 0,
            "error_messages": [],
            "error_lines": [],
            "commands_with_errors": []
        }
        
        for i, command in enumerate(parsed_script):
            if not command.get("parsed", True):
                error_details["total_errors"] += 1
                error_details["error_messages"].append(command.get("parsed_comment", "Unknown error"))
                error_details["error_lines"].append(i + 1)  # 1-based line numbering
                error_details["commands_with_errors"].append({
                    "line": i + 1,
                    "command": command.get("command", "UNKNOWN"),
                    "error": command.get("parsed_comment", "Unknown error")
                })
        
        # Create formatted error text
        if error_details["total_errors"] > 0:
            formatted_errors = []
            for cmd_error in error_details["commands_with_errors"]:
                formatted_errors.append(f"Line {cmd_error['line']} ({cmd_error['command']}): {cmd_error['error']}")
            
            error_details["formatted_text"] = "\n".join(formatted_errors)
            return True, "not clear", currentFuncName(), error_details
        else:
            error_details["formatted_text"] = ""
            return True, "clear", currentFuncName(), error_details
            
    except Exception as e:
        error_message = f"{currentFuncName()} error: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), {}


def validate_parsed_script_structure(parsed_script: dict) -> tuple[bool, str]:
    """
    Validate the structure of a parsed script to ensure it has the expected format.
    
    Args:
        parsed_script (dict): The parsed script to validate
        
    Returns:
        tuple: (is_valid: bool, error_message: str)
    """
    if not isinstance(parsed_script, (list, dict)):
        return False, "Parsed script must be a list or dict"
    
    if isinstance(parsed_script, dict):
        # If it's a dict, check if it has the expected structure
        if "parsed" not in parsed_script:
            return False, "Parsed script dict must contain 'parsed' key"
        return True, ""
    
    # If it's a list, validate each command
    for i, command in enumerate(parsed_script):
        if not isinstance(command, dict):
            return False, f"Command at index {i} must be a dict"
        
        if "parsed" not in command:
            return False, f"Command at index {i} must contain 'parsed' key"
        
        if not command["parsed"] and "parsed_comment" not in command:
            return False, f"Unparsed command at index {i} must contain 'parsed_comment' key"
    
    return True, ""



