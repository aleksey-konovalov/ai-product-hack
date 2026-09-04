import os
import sys
import logging
from logging.handlers import RotatingFileHandler

# Безопасный импорт colorlog с фоллбэком
try:
    from colorlog import ColoredFormatter
    _HAS_COLORLOG = True
except ImportError:
    _HAS_COLORLOG = False

def get_logger(
    name: str = "app", 
    log_level: int = None,
    log_file: str = None
) -> logging.Logger:
    """
    Создает и настраивает единый логгер.
    
    name: Имя логгера (__name__ или имя сервиса)
    log_level: Уровень логирования (по умолчанию берется из ENV LOG_LEVEL или INFO)
    log_file: Имя файла для логов (по умолчанию <name>.log)
    """
    # Защита от дублирования хендлеров при повторном импорте (важно для микросервисов)
    logger = logging.getLogger(name)
    
    # Если у логгера уже есть хендлеры, ничего не добавляем
    if logger.handlers:
        return logger

    # Проверяем родительские логгеры (чтобы дочерние не дублировали вывод)
    parent = logger.parent
    while parent is not None:
        if parent.handlers:
            # Устанавливаем уровень родителя, но хендлеры не добавляем
            logger.setLevel(parent.level)
            return logger
        parent = parent.parent
        
    # Уровень логирования (поддерживает как числа, так и строки тип 'DEBUG')
    if log_level is not None:
        level = log_level
    else:
        env_level = os.getenv("LOG_LEVEL", "INFO")
        # Если в env число (например, "20")
        if env_level.isdigit():
            level = int(env_level)
        # Если в env слово (например, "DEBUG")
        else:
            level = getattr(logging, env_level.upper(), logging.INFO)

    logger.setLevel(level)

    # Форматтеры
    base_formatter = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # Проверяем, выводим ли мы в реальную консоль (TTY) и есть ли colorlog
    use_colors = _HAS_COLORLOG and sys.stdout.isatty()
    
    if use_colors:
        console_formatter = ColoredFormatter(
            "%(log_color)s%(levelname)-8s%(reset)s %(asctime)s - %(message)s",
            datefmt="%H:%M:%S",
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'red,bg_white',
            }
        )
    else:
        # Фоллбэк: обычный форматтер, если colorlog не установлен или это Docker/CI
        console_formatter = logging.Formatter(
            "%(levelname)-8s %(asctime)s - %(message)s",
            datefmt="%H:%M:%S"
        )

    # Обработчик для консоли
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # Обработчик для файла (зависит от ENV переменной LOG_TO_FILE)
    log_to_file = os.getenv("LOG_TO_FILE", "false").lower() in ("true", "1", "yes")
    
    if log_to_file:
        log_dir = os.getenv("LOG_DIR", os.path.join(os.getcwd(), "logs"))
        os.makedirs(log_dir, exist_ok=True)
        
        filename = log_file or f"{name.split('.')[-1]}.log"
        log_path = os.path.join(log_dir, filename)
        
        file_handler = RotatingFileHandler(
            filename=log_path,
            maxBytes=5*1024*1024,  # 5 MB
            backupCount=3,
            encoding='utf-8'
        )
        # В файл всегда пишем DEBUG, чтобы не терять полезную инфу
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(base_formatter))
        logger.addHandler(file_handler)

    # Для FastAPI/Uvicorn: предотвращает двойное логирование
    logger.propagate = False

    return logger

# Экземпляр логгера по умолчанию для удобного импорта
app_logger = get_logger(os.getenv("SERVICE_NAME", "app"))

