from config import BOOKING_STATUS_LABELS

ADMIN_ONLY_TEXT = "У вас нет доступа к этой команде."
BACKUP_OK_TEXT = "Резервная копия создана: {path}"
BACKUP_ERROR_TEXT = "Не удалось создать резервную копию: {error}"

MAIN_MENU_TEXT = "Главное меню"
BOOKING_RULES_TEXT = "Правила бронирования и оплаты"
PAYMENT_DESCRIPTION_TEXT = (
    "После подтверждения заявки нужно отправить скриншот оплаты в течение срока оплаты."
)

__all__ = [
    "ADMIN_ONLY_TEXT",
    "BACKUP_ERROR_TEXT",
    "BACKUP_OK_TEXT",
    "BOOKING_RULES_TEXT",
    "BOOKING_STATUS_LABELS",
    "MAIN_MENU_TEXT",
    "PAYMENT_DESCRIPTION_TEXT",
]
