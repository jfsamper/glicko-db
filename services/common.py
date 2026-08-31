"""Service for common utilities and shared functions across the application."""
import json
import hashlib
import os
import secrets
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone as datetime_timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import request, session
from werkzeug.security import check_password_hash, generate_password_hash

from services.player_stats import (
    build_player_result_summary
)

from config import (
    ADMIN_PASSWORD,
    DB_PATH,
    DEFAULT_TIMEZONE,
    MAIL_FROM,
    MAIL_PASSWORD,
    MAIL_PORT,
    MAIL_SERVER,
    MAIL_USERNAME,
    MAIL_USE_TLS,
    PASSWORD_RESET_TTL_SECONDS,
)

ALLOWED_ROLES = ("administrator", "tournament_director", "operator")
AUDIT_RETENTION_DAYS = max(1, int(os.environ.get("AUDIT_RETENTION_DAYS", "730")))
AUDIT_DETAILS_MAX_BYTES = 2048


def get_configured_timezone(user_id=None):
    if user_id is None:
        try:
            user_id = session.get("user_id")
        except RuntimeError:
            user_id = None
    if user_id is None:
        return DEFAULT_TIMEZONE

    conn = get_db()
    try:
        try:
            row = conn.execute(
                "SELECT timezone FROM users WHERE id = ? AND is_active = 1",
                (user_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return DEFAULT_TIMEZONE
    finally:
        conn.close()

    timezone_name = row["timezone"] if row is not None else None
    if not timezone_name:
        return DEFAULT_TIMEZONE
    try:
        return ZoneInfo(timezone_name)
    except (TypeError, ZoneInfoNotFoundError):
        return DEFAULT_TIMEZONE


def validate_timezone(timezone_name):
    if timezone_name in (None, ""):
        return None
    if not isinstance(timezone_name, str):
        raise ValueError("unsupported timezone")
    try:
        ZoneInfo(timezone_name)
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError("unsupported timezone") from exc
    return timezone_name


def current_datetime(user_id=None):
    return datetime.now(get_configured_timezone(user_id))


def current_date(user_id=None):
    return current_datetime(user_id).date()


def server_date():
    """Return the calendar date used for period calculations and data defaults."""
    return datetime.now(DEFAULT_TIMEZONE).date()


def current_timestamp(user_id=None):
    return current_datetime(user_id).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def timestamp_days_ago(days, user_id=None):
    return (current_datetime(user_id) - timedelta(days=days)).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

TRANSLATIONS = {
    "es": {
        "title": "bdACG",
        "subtitle": "Asociación Colombiana de Go",
        "database": "Base de datos de la ",
        "home": "Inicio",
        "rankings": "Posiciones",
        "players": "Jugadores",
        "import": "Importar",
        "language": "Idioma",
        "english": "English",
        "spanish": "Español",
        "portuguese": "Português",
        "team": "Equipo",
        "news": "Noticias",
        "hero_title": "Portal de Rangos",

        "hero_text": "Este portal ayuda a manejar la base de datos de jugadores, partidas y rankings para la comunidad.",
        "hero_text2": "Los jugadores pueden revisar sus perfiles y ver su historial de partidas.",
        "cta_rankings": "Abrir rankings",
        "cta_players": "Ver jugadores",
        "stats_title": "Estadísticas de la comunidad",
        "stats": [
            "Rankings de todo momento, año y trimestre",
            "Jugadores más activos y mayores subidas de Glicko",
            "Victorias como blancas y como negras"
        ],
        "stats_badge_top_rated": "Mejor Rating",
        "stats_period_all_time": "Todos los Tiempos",
        "stats_period_year": "Año",
        "stats_period_quarter": "Trimestre",
        "stats_metric_active": "Más Activo",
        "stats_metric_wins": "Más Victorias",
        "stats_metric_glicko": "Mayor Progreso",
        "stats_metric_white": "Más victorias como blancas",
        "stats_metric_black": "Más victorias como negras",
        "stats_empty": "Todavía no hay partidas en este periodo.",
        "rankings_heading": "Posiciones Actuales",
        "rankings_subtitle": "Ordenados por rating Glicko",
        "table_heading": "Tabla de Resultados",
        "position": "Posición",
        "player": "Jugador",
        "rating": "Glicko",
        "category": "Categoría",
        "opponent": "Oponente",
        "opponent_records": "Resultados vs Oponentes",
        "rd": "RD",
        "games": "Partidas",
        "recent_form": "Resultados recientes",
        "recent_change": "Cambio reciente",
        "recent_results": "Resultados recientes",
        "total_results": "Resultados totales",
        "yearly_results": "Resultados anuales",
        "quarterly_results": "Resultados trimestrales",
        "overall_results": "Totales",
        "win_rate": "Porcentaje de victorias",
        "recent_streak": "Última racha",
        "career_milestones": "Hitos",
        "best_rating": "Mejor rating",
        "last_played": "Última partida",
        "streak_count": "Racha más larga",
        "as_white": "Como blancas",
        "as_black": "Como negras",
        "source_note": "Solamente se muestran los jugadores activos",
        "players_heading": "Directorio de jugadores",
        "players_subtitle": "Explora y revisa perfiles de jugadores",
        "profile_heading": "Perfil del jugador",
        "profile_subtitle": "Rating actual, estadística e historial",
        "season": "Temporada",
        "all_seasons": "Todas las temporadas",
        "all_categories": "Todas las categorías",
        "current_streak": "Racha actual de victorias",
        "tournament_overview": "Resumen de Torneos",
        "no_tournament_history": "No hay historial de torneos.",
        "seed_rank": "Cabeza de serie",
        "final_position": "Posición final",
        "current_rating": "Rating actual",
        "games_played": "Partidas jugadas",
        "wins": "Victorias",
        "losses": "Derrotas",
        "draws": "Tablas",
        "win_pct": "% victorias",
        "match_history": "Historial de partidas",
        "date": "Fecha",
        "time_round": "Ronda",
        "white": "Blancas",
        "black": "Negras",
        "winner": "Ganador",
        "event": "Evento",
        "result": "Resultado",
        "result_win": "Victoria",
        "result_loss": "Derrota",
        "result_draw": "Tablas",
        "chart_title": "Historial de rating",
        "chart_caption": "El gráfico muestra la evolución del rating del jugador con el tiempo.",
        "best_rating": "Mejor rating",
        "player_badges_heading": "Logros",
        "country_colombia": "Colombia",
        "badge_rank_prefix": "#",

        "glicko_scale": "Escala Glicko",
        "glicko_label": "Glicko",
        "formula": "Fórmula",
        "category_parameters": "Parámetros de Glicko",
        "constant": "constante",
        "minimum": "mínimo",
        "dan_label": "dan",
        "kyu_label": "kyu",
        "required_columns_missing": "Faltan columnas obligatorias",
        "unsupported_file_format": "Formato de archivo no compatible",
        "invalid_date_format": "Formato de fecha no válido (usa AAAA-MM-DD)",

        "period_all_time": "Todo el tiempo",
        "period_year": "Este año",
        "period_quarter": "Este trimestre",
        "period_current": "Actual",

        "baseline": "Línea base 1500",
        "edit_heading": "Editar",
        "edit_subtitle": "Actualiza el rating, RD y volatilidad del jugador",
        "delete_heading": "Eliminar",
        "import_heading": "Importar partidas",
        "import_subtitle": "Excel (CSV o XLSX) y OpenGotha(XML)",
        "upload_file": "Subir archivo",
        "submit": "Importar",
        "delete_confirmation": "¿Eliminar este jugador y todas las partidas relacionadas? Esta acción no se puede deshacer.",
        "success": "Cambio realizado",
        "error": "El cambio falló",
        "no_file": "Elige un archivo",
        "admin_login_heading": "Acceso de administrador",
        "password_label": "Contraseña",
        "login_button": "Ingresar",
        "logout_button": "Cerrar sesión",
        "profile_title": "Mi perfil",
        "email_label": "Correo electrónico",
        "language_label": "Idioma predeterminado",
        "language_names": {"es": "Español", "en": "Inglés", "pt": "Portugués"},
        "theme_label": "Tema",
        "theme_names": {"light": "Claro", "dark": "Oscuro"},
        "change_password_heading": "Cambiar contraseña",
        "current_password_label": "Contraseña actual",
        "new_password_label": "Nueva contraseña",
        "confirm_password_label": "Confirmar contraseña",
        "save_profile_button": "Guardar perfil",
        "profile_updated_success": "Perfil actualizado",
        "current_password_invalid": "La contraseña actual no es correcta",
        "password_too_short": "La contraseña debe tener al menos 8 caracteres",
        "password_mismatch": "Las contraseñas no coinciden",
        "invalid_email": "El correo electrónico no es válido",
        "email_taken": "El correo electrónico ya está en uso",
        "invalid_language": "El idioma no es válido",
        "invalid_theme": "El tema no es válido",
        "forgot_password_link": "¿Olvidaste tu contraseña?",
        "forgot_password_title": "Recuperar contraseña",
        "send_reset_button": "Enviar enlace",
        "password_reset_requested": "Si existe una cuenta con ese correo, recibirá un enlace de recuperación.",
        "reset_password_title": "Establecer nueva contraseña",
        "reset_password_button": "Cambiar contraseña",
        "password_reset_success": "Contraseña actualizada",
        "invalid_reset_token": "El enlace no es válido o ya expiró",
        "invalid_password": "Contraseña incorrecta",
        "welcome_user": "Bienvenido",
        "admin_menu_link": "Menú de administración",
        "admin_heading": "Administración",
        "admin_tournament_operations_heading": "Operaciones de torneos",
        "admin_data_management_heading": "Gestión de datos",
        "admin_management_heading": "Administración y acceso",
        "admin_settings_title": "Configuración de la aplicación",
        "admin_settings_desc": "Ajusta los límites de acceso y recuperación de cuentas.",
        "security_settings_heading": "Seguridad y recuperación",
        "max_login_attempts_label": "Intentos de inicio de sesión permitidos",
        "login_window_seconds_label": "Ventana de inicio de sesión (segundos)",
        "password_reset_ttl_seconds_label": "Duración del enlace de recuperación (segundos)",
        "invalid_application_settings": "La configuración no es válida",
        "back_to_admin_index": "Volver al índice de administración",
        "back_to_tournament": "Volver al torneo",
        "back_to_tournament_list": "Volver a la lista de torneos",
        "admin_categories_title": "Categorías",
        "admin_categories_desc": "Configura la conversión de categorías.",
        "admin_import_title": "Importar",
        "admin_import_desc": "Importa archivos Excel, CSV y OpenGotha.",
        "admin_matches_title": "Partidas",
        "admin_matches_desc": "Consulta y gestiona partidas.",
        "admin_players_title": "Jugadores",
        "admin_players_desc": "Filtra y gestiona jugadores.",
        "admin_ratings_title": "Ratings",
        "admin_ratings_desc": "Configura y recalcula ratings.",
        "tournaments_title": "Torneos",
        "public_tournaments_heading": "Torneos públicos",
        "public_tournaments_subtitle": "Consulta emparejamientos, resultados por ronda y clasificación.",
        "no_public_tournaments": "No hay torneos públicos disponibles.",
        "show_drafts": "Mostrar borradores",
        "hide_drafts": "Ocultar borradores",
        "tournaments_desc": "Crea torneos y gestiona emparejamientos.",
        "tournament_name": "Nombre del torneo",
        "tournament_description": "Descripción del torneo",
        "settings": "Ajustes",
        "pairing_system": "Sistema de emparejamiento",
        "tournament_type": "Tipo de torneo",
        "bye_points": "Puntos por descanso",
        "absent_points": "Puntos por ausencia",
        "handicap_stones_label": "Piedras de hándicap",
        "handicap_tournament_label": "Hándicap",
        "acceleration_categories": "Categorías límite de aceleración",
        "acceleration_category_count": "Número de categorías límite",
        "acceleration_category_floor": "Límite inferior",
        "acceleration_rounds": "Rondas con aceleración",
        "category_sections": "Secciones por categoría",
        "category_rounds": "Rondas con secciones estrictas",
        "handicap_status_enabled": "Sí",
        "handicap_status_disabled": "No",
        "handicap_apply_existing_confirmation": "¿Aplicar el hándicap automático a los emparejamientos existentes? Si eliges No, se pondrán a 0.",
        "standings": "Clasificación",
        "score": "Puntuación",
        "mms": "MMS",
        "sos": "SOS",
        "sosos": "SOSOS",
        "sodos": "SODOS",
        "swiss": "Suizo",
        "swiss_cat": "Suizo por categoría",
        "accelerated_swiss": "Suizo acelerado",
        "mcmahon": "McMahon",
        "rounds": "Rondas",
        "location": "Lugar",
        "create_tournament": "Crear torneo",
        "new_player_option": "Crear jugador nuevo",
        "set_player": "Asignar jugador",
        "import_opengotha": "Importar XML de OpenGotha",
        "tournament_file": "Archivo XML del torneo",
        "generate_round": "Generar próxima ronda",
        "tournament_pairings": "Emparejamientos del torneo",
        "tournament_round": "Ronda",
        "board": "Mesa",
        "bye": "Descanso",
        "absent": "Ausente",
        "status": "Estado",
        "no_tournaments": "No se encontraron torneos.",
        "unpaired_players": "Jugadores sin emparejar",
        "paired_tables": "Mesas emparejadas",
        "no_unpaired_players": "Todos los jugadores están emparejados en esta ronda.",
        "no_paired_tables": "No hay mesas emparejadas en esta ronda.",
        "delete_tournament_heading": "Eliminar torneo",
        "delete_tournament_confirmation": "¿Eliminar este torneo y todas sus rondas, emparejamientos y jugadores? Esta acción no se puede deshacer.",
        "cancel_button": "Cancelar",
        "participant_list": "Lista de jugadores",
        "player_management": "Gestionar jugadores",
        "add_player": "Agregar jugador",
        "create_player": "Crear jugador",
        "player_created": "Jugador creado y agregado al torneo",
        "create_pending_player": "Crear jugador pendiente",
        "pending_player_created": "Jugador pendiente creado para este torneo",
        "player_already_exists": "Ya existe un jugador con este nombre: {name}",
        "similar_player_exists": "Ya existe un jugador con un nombre similar: {name}",
        "pending_player_deleted": "Jugador pendiente eliminado",
        "pending_player_delete_confirmation": "¿Eliminar este jugador pendiente? Esta acción no se puede deshacer.",
        "player_name_required": "El nombre del jugador es obligatorio",
        "invalid_rating": "El rating inicial debe ser mayor que cero",
        "invalid_rank": "El rango debe ser un número entero mayor que cero",
        "rank_label": "Rango",
        "remove_player": "Eliminar jugador",
        "manual_pair": "Emparejamiento manual",
        "unpair": "Desemparejar",
        "pair_selected": "Emparejar seleccionados",
        "round_results": "Resultados de la ronda",
        "mark_absent": "Marcar ausente",
        "select_round": "Seleccionar ronda",
        "unpair": "Desemparejar",
        "no_rounds": "Genera una ronda antes de crear emparejamientos manuales.",
        "system_statistics": "Estadísticas del sistema",
        "snapshots": "Capturas",
        "glicko_formula": "Fórmula Glicko-2",
        "rating_parameters": "Parámetros de rating",
        "tau_label": "TAU",
        "default_rating_label": "Rating predeterminado",
        "default_rd_label": "RD predeterminado",
        "default_volatility_label": "Volatilidad predeterminada",
        "save_parameters": "Guardar parámetros",
        "reset_to_default": "Restablecer valores predeterminados",
        "reset_to_default_success": "Valores restablecidos a los predeterminados",
        "incremental_update": "Actualización incremental",
        "ratings_out_of_date_since": "Los ratings están desactualizados desde:",
        "update_latest_snapshot": "Actualizar desde la última instantánea",
        "update_latest_snapshot_confirm": "¿Actualizar los ratings desde la última instantánea?",
        "ratings_up_to_date": "Los ratings están actualizados.",
        "recalculate_ratings": "Recalcular ratings",
        "recalculate_description": "Reconstruye los ratings y el historial a partir de todas las partidas importadas, comenzando con el rating inicial de cada jugador.",
        "recalculate_confirm": "¿Recalcular todos los ratings y reconstruir el historial?",
        "recalculate_everything": "Recalcular todo",
        "admin_backups_title": "Copias de seguridad",
        "admin_backups_desc": "Crea y restaura copias de seguridad.",
        "admin_users_title": "Gestión de usuarios",
        "admin_users_desc": "Gestiona cuentas y roles.",
        "audit_review_heading": "Revisión de auditoría",
        "audit_review_subtitle": "Revisa acciones administrativas con filtros por usuario y tipo.",
        "audit_review_desc": "Consulta las acciones administrativas.",
        "audit_review_empty": "No hay eventos de auditoría para mostrar.",
        "actor_label": "Usuario",
        "action_label": "Acción",
        "resource_label": "Recurso",
        "timestamp_label": "Fecha y hora",
        "details_label": "Detalles",
        "all_users": "Todos los usuarios",
        "all_actions": "Todas las acciones",
        "audit_search_label": "Buscar",
        "audit_date_from": "Desde",
        "audit_date_to": "Hasta",
        "user_management_heading": "Gestión de usuarios",
        "user_management_subtitle": "Consulta, crea, edita y elimina usuarios con acceso administrativo.",
        "create_user_heading": "Crear usuario",
        "edit_user_heading": "Editar usuario",
        "username_label": "Usuario",
        "role_label": "Rol",
        "timezone_label": "Zona horaria",
        "timezone_default": "Predeterminada (UTC-05:00)",
        "invalid_timezone": "La zona horaria no es válida.",
        "active_label": "Activo",
        "create_user_button": "Crear usuario",
        "edit_user_button": "Guardar cambios",
        "delete_user_button": "Eliminar",
        "cancel_button": "Cancelar",
        "user_created_success": "Usuario creado correctamente.",
        "user_updated_success": "Usuario actualizado correctamente.",
        "user_deleted_success": "Usuario eliminado correctamente.",
        "user_username_required": "El nombre de usuario es obligatorio.",
        "user_username_taken": "Ese nombre de usuario ya existe.",
        "inactive_label": "Inactivo",
        "no_users": "No hay usuarios disponibles.",
        "delete_user_confirm": "¿Eliminar este usuario?",
        "open_link": "Abrir",
        "matches_heading_admin": "Gestión de Partidas",
        "manage_matches_subtitle": "Agrega, edita y elimina.",
        "add_match": "Agregar partida",
        "process_matches": "Procesar partidas",
        "pending_new_players": "Nuevos jugadores pendientes",
        "pending_new_players_warning": "Este torneo creará {count} usuarios nuevos al procesar las partidas.",
        "pending_new_players_hint": "Se recomienda revisar los nombres sugeridos antes de procesar.",
        "pending_label": "pendiente",
        "did_you_mean": "¿Quisiste decir",
        "no_matches_found": "No se encontraron partidas.",
        "edit_player_heading": "Editar jugador",
        "edit_player_subtitle": "Actualiza la información del jugador.",
        "display_name_label": "Nombre para mostrar",
        "first_name_label": "Nombre",
        "last_name_label": "Apellido",
        "slug_label": "Slug",
        "save_changes": "Guardar cambios",
        "change": "Cambio",
        "ratings_heading": "Ratings",
        "refresh_stats_title": "Actualizar estadísticas",
        "refresh_stats_desc": "Recalcula victorias, derrotas y partidas jugadas.",
        "refresh_button": "Actualizar",
        "new": "Nuevo",
        "preview": "Vista previa",
        "admin_categories_preview_desc": "Vista previa de los cambios en las categorías. La muestra incluye los 5 jugadores con mayor rating y jugadores activos aleatorios.",
        "recalculate_ratings_title": "Recalcular ratings",
        "recalculate_ratings_desc": "Reconstruye los ratings a partir del historial de partidas.",
        "recalculate_button": "Recalcular",
        "rebuild_everything_title": "Reconstruir todo",
        "rebuild_everything_desc": "Reconstrucción completa de ratings y estadísticas.",
        "rebuild_button": "Reconstruir",
        "backups_heading": "Copias de seguridad",
        "backups_subtitle": "Crea y restaura copias de seguridad de la base de datos.",
        "create_backup_button": "Crear copia de seguridad",
        "name_label": "Nombre",
        "page_size_label": "Resultados por página",
        "glicko_range_label": "Rango Glicko",
        "last_active": "Última actividad",
        "any_time": "En cualquier momento",
        "last_30_days": "Últimos 30 días",
        "last_90_days": "Últimos 90 días",
        "last_year": "Último año",
        "page_label": "Página",
        "showing_results": "Mostrando resultados",
        "of_label": "de",
        "prev_page": "Anterior",
        "next_page": "Siguiente",
        "actions_label": "Acciones",
        "no_backups": "No hay copias de seguridad disponibles.",
        "category_converter_heading": "Glicko \u2192 Categoría",
        "glicko_input_placeholder": "Ingresa el rating Glicko",
        "glicko_input_label": "Rating Glicko",
        "convert_button": "Convertir",
        "clear_search": "Limpiar",
        "search": "Buscar",
        "sort_label": "Ordenar por",
        "order_label": "Orden",
        "desc_label": "Descendente",
        "asc_label": "Ascendente",
        "add_match_heading": "Agregar partida",
        "edit_match_heading": "Editar partida",
        "match_date_label": "Fecha",
        "white_player_label": "Blancas",
        "black_player_label": "Negras",
        "result_label": "Resultado",
        "event_label": "Evento",
        "notes_label": "Notas",
        "select_player": "Selecciona un jugador",
        "white_wins": "Ganan blancas (1-0)",
        "black_wins": "Ganan negras (0-1)",
        "draw_result": "Tablas (1/2-1/2)",
        "save_match": "Guardar partida",
        "delete_match_confirmation": "¿Eliminar esta partida? Esta acción no se puede deshacer.",
        "same_player_error": "Blancas y negras deben ser jugadores distintos",
        "footer": "dbACG 2026",
        "contact": "Contacto",
        "report_results": "Reportar resultados",
        "report_results_help": "Si organiza torneos, puede reportar los resultados",
        "reports_title": "Reportes",
        "export_report": "Exportar",
        "export_report_pdf": "Exportar PDF",
        "export_report_csv": "Exportar CSV",
        "report_period": "Periodo",
        "report_start_date": "Fecha inicial",
        "report_end_date": "Fecha final",
        "report_player": "Jugador",
        "report_player_export": "Exportar informe de jugador",
        "all_players": "Todos los jugadores",
        "apply_filters": "Aplicar",
        "period_month": "Este mes",
        "period_custom": "Personalizado",
        "report_players": "Rendimiento por jugador",
        "matches_total": "Partidas",
        "rating_change_points": "Puntos de rating",
        "rating_change_percentage": "% de rating",
        "category_change": "Cambio de categoría",
        "report_countries": "Rendimiento por país del oponente",
        "report_clubs": "Rendimiento por club del oponente",
        "report_empty": "No hay juegos en este periodo.",
        "report_excluded": "Registros excluidos",
        "country": "País",
        "club": "Club",
        "here": "aquí",
        "no_rating_history": "No hay historial de rating desde 2020.",
        "category_nav": "Categoría",
        "export_results": "Exportar resultados",
        "completed": "Completado",
        "active": "En curso",
        "ongoing": "En curso",
        "canceled": "Cancelado",
        "draft": "Borrador"
    },
    "en": {
        "title": "ACGdb",
        "subtitle": "Colombian Go Association",
        "database": "Database for the ",
        "home": "Home",
        "rankings": "Rankings",
        "players": "Players",
        "import": "Import",
        "language": "Language",
        "english": "English",
        "spanish": "Español",
        "portuguese": "Português",
        "team": "Team",
        "news": "News",
        "hero_title": "Launch the ratings portal",
        "hero_text": "This portal helps manage the player, match, and ranking database for the community.",
        "hero_text2": "Players can review their profiles and see their match history.",
        "cta_rankings": "Open rankings",
        "cta_players": "Browse players",
        "stats_title": "Community Stats",
        "stats": [
            "All-time, yearly, and quarterly rankings",
            "Most active players and biggest Glicko gains",
            "Wins as white and as black"
        ],
        "stats_badge_top_rated": "Best rating",
        "stats_period_all_time": "All Time",
        "stats_period_year": "Current Year",
        "stats_period_quarter": "Current Quarter",
        "stats_metric_active": "Most Active",
        "stats_metric_wins": "Most Wins",
        "stats_metric_glicko": "Biggest Improvement",
        "stats_metric_white": "Most wins as white",
        "stats_metric_black": "Most wins as black",
        "stats_empty": "No matches in this period yet.",
        "rankings_heading": "Current rankings",
        "rankings_subtitle": "Sorted by current Glicko rating",
        "table_heading": "Results Table",
        "position": "Rank",
        "player": "Player",
        "rating": "Rating",
        "category": "Category",
        "opponent": "Opponent",
        "opponent_records": "Results vs Opponents",
        "rd": "RD",
        "games": "Games",
        "recent_form": "Recent form",
        "recent_change": "Recent change",
        "recent_results": "Recent results",
        "total_results": "Total results",
        "yearly_results": "Yearly results",
        "quarterly_results": "Quarterly results",
        "change": "Change",
        "overall_results": "Overall",
        "win_rate": "Win rate",
        "recent_streak": "Latest streak",
        "career_milestones": "Career milestones",
        "best_rating": "Best rating",
        "last_played": "Last played",
        "streak_count": "Longest streak",
        "as_white": "As White",
        "as_black": "As Black",
        "source_note": "Only active players are shown",
        "players_heading": "Players directory",
        "players_subtitle": "Search and review player profiles",
        "profile_heading": "Player profile",
        "profile_subtitle": "Current rating, stats, and match history",
        "season": "Season",
        "all_seasons": "All seasons",
        "all_categories": "All categories",
        "current_streak": "Current winning streak",
        "tournament_overview": "Tournament Overview",
        "no_tournament_history": "No tournament history.",
        "seed_rank": "Seed",
        "final_position": "Final position",
        "current_rating": "Current rating",
        "games_played": "Games played",
        "wins": "Wins",
        "losses": "Losses",
        "draws": "Draws",
        "win_pct": "Win %",
        "match_history": "Match history",
        "date": "Date",
        "time_round": "Round",
        "white": "White",
        "black": "Black",
        "winner": "Winner",
        "event": "Event",
        "result": "Result",
        "result_win": "Win",
        "result_loss": "Loss",
        "result_draw": "Draw",
        "chart_title": "Rating history",
        "chart_caption": "The chart shows the player rating over time as matches are imported.",
        "baseline": "1500 baseline",
        "best_rating": "Best rating",
        "player_badges_heading": "Achievements",
        "country_colombia": "Colombia",
        "badge_rank_prefix": "#",
        "category_nav": "Category",

        "glicko_scale": "Glicko Scale",
        "glicko_label": "Glicko",
        "formula": "Formula",
        "category_parameters": "Glicko Parameters",
        "constant": "constant",
        "minimum": "minimum",
        "dan_label": "dan",
        "kyu_label": "kyu",
        "required_columns_missing": "Required columns are missing",
        "unsupported_file_format": "Unsupported file format",
        "invalid_date_format": "Invalid date format (use YYYY-MM-DD)",

        "period_all_time": "All time",
        "period_year": "This year",
        "period_quarter": "This quarter",
        "period_current": "Current",
        "edit_heading": "Edit",
        "delete_heading": "Delete",
        "import_heading": "Import matches",
        "import_subtitle": "Excel (CSV or XLSX) and OpenGotha(XML).",
        "upload_file": "Upload file",
        "submit": "Import",
        "delete_confirmation": "Delete this player and all related matches? This action cannot be undone.",
        "success": "Edit completed",
        "error": "Edit failed",
        "no_file": "Please choose a file",
        "edit_subtitle": "Update player rating, RD, and volatility",
        "admin_login_heading": "Admin Login",
        "password_label": "Password",
        "login_button": "Login",
        "logout_button": "Logout",
        "profile_title": "My profile",
        "email_label": "Email",
        "language_label": "Default language",
        "language_names": {"es": "Spanish", "en": "English", "pt": "Portuguese"},
        "theme_label": "Theme",
        "theme_names": {"light": "Light", "dark": "Dark"},
        "change_password_heading": "Change password",
        "current_password_label": "Current password",
        "new_password_label": "New password",
        "confirm_password_label": "Confirm password",
        "save_profile_button": "Save profile",
        "profile_updated_success": "Profile updated",
        "current_password_invalid": "The current password is incorrect",
        "password_too_short": "The password must be at least 8 characters",
        "password_mismatch": "The passwords do not match",
        "invalid_email": "The email address is invalid",
        "email_taken": "That email address is already in use",
        "invalid_language": "The language is invalid",
        "invalid_theme": "The theme is invalid",
        "forgot_password_link": "Forgot your password?",
        "forgot_password_title": "Recover password",
        "send_reset_button": "Send link",
        "password_reset_requested": "If an account exists for that email, it will receive a recovery link.",
        "reset_password_title": "Set a new password",
        "reset_password_button": "Change password",
        "password_reset_success": "Password updated",
        "invalid_reset_token": "This link is invalid or has expired",
        "invalid_password": "Invalid password",
        "welcome_user": "Welcome",
        "admin_menu_link": "Admin Menu",
        "admin_heading": "Admin",
        "admin_tournament_operations_heading": "Tournament operations",
        "admin_data_management_heading": "Data management",
        "admin_management_heading": "Administration and access",
        "admin_settings_title": "Application settings",
        "admin_settings_desc": "Adjust account access and recovery limits.",
        "security_settings_heading": "Security and recovery",
        "max_login_attempts_label": "Allowed login attempts",
        "login_window_seconds_label": "Login window (seconds)",
        "password_reset_ttl_seconds_label": "Recovery link lifetime (seconds)",
        "invalid_application_settings": "The settings are invalid",
        "back_to_admin_index": "Back to admin index",
        "back_to_tournament": "Back to tournament",
        "back_to_tournament_list": "Back to tournament list",
        "admin_categories_title": "Categories",
        "admin_categories_desc": "Configure category conversion.",
        "admin_import_title": "Import",
        "admin_import_desc": "Import Excel, CSV, and OpenGotha files.",
        "admin_matches_title": "Matches",
        "admin_matches_desc": "View and manage matches.",
        "admin_players_title": "Players",
        "admin_players_desc": "Filter and manage players.",
        "admin_ratings_title": "Ratings",
        "admin_ratings_desc": "Configure and recalculate ratings.",
        "tournaments_title": "Tournaments",
        "public_tournaments_heading": "Public tournaments",
        "public_tournaments_subtitle": "View pairings, round results, and standings.",
        "no_public_tournaments": "No public tournaments are available.",
        "show_drafts": "Show drafts",
        "hide_drafts": "Hide drafts",
        "tournaments_desc": "Create tournaments and manage pairings.",
        "tournament_name": "Tournament name",
        "tournament_description": "Tournament description",
        "settings": "Settings",
        "pairing_system": "Pairing system",
        "tournament_type": "Tournament type",
        "bye_points": "BYE points",
        "absent_points": "Absent points",
        "handicap_stones_label": "Handicap stones",
        "handicap_tournament_label": "Handicap",
        "acceleration_categories": "Acceleration limit categories",
        "acceleration_category_count": "Number of limit categories",
        "acceleration_category_floor": "Lower limit",
        "acceleration_rounds": "Rounds with acceleration",
        "category_sections": "Category sections",
        "category_rounds": "Rounds with strict sections",
        "handicap_status_enabled": "Yes",
        "handicap_status_disabled": "No",
        "handicap_apply_existing_confirmation": "Apply automatic handicap to existing pairings? Choosing No will set them to 0.",
        "standings": "Standings",
        "score": "Score",
        "mms": "MMS",
        "sos": "SOS",
        "sosos": "SOSOS",
        "sodos": "SODOS",
        "swiss": "Swiss",
        "swiss_cat": "Swiss by category",
        "accelerated_swiss": "Accelerated Swiss",
        "mcmahon": "McMahon",
        "rounds": "Rounds",
        "location": "Location",
        "create_tournament": "Create tournament",
        "new_player_option": "Create new player",
        "set_player": "Set Player",
        "import_opengotha": "Import OpenGotha XML",
        "tournament_file": "Tournament XML file",
        "generate_round": "Generate next round",
        "tournament_pairings": "Tournament pairings",
        "tournament_round": "Round",
        "board": "Board",
        "bye": "Bye",
        "absent": "Absent",
        "status": "Status",
        "no_tournaments": "No tournaments found.",
        "unpaired_players": "Unpaired players",
        "paired_tables": "Paired tables",
        "no_unpaired_players": "All players are paired in this round.",
        "no_paired_tables": "No tables are paired in this round.",
        "delete_tournament_heading": "Delete tournament",
        "delete_tournament_confirmation": "Delete this tournament and all its rounds, pairings, and players? This action cannot be undone.",
        "participant_list": "Player list",
        "player_management": "Manage players",
        "add_player": "Add player",
        "create_player": "Create player",
        "player_created": "Player created and added to the tournament",
        "create_pending_player": "Create pending player",
        "pending_player_created": "Pending player created for this tournament",
        "player_already_exists": "A player with this name already exists: {name}",
        "similar_player_exists": "A player with a similar name already exists: {name}",
        "pending_player_deleted": "Pending player deleted",
        "pending_player_delete_confirmation": "Delete this pending player? This action cannot be undone.",
        "player_name_required": "Player name is required",
        "invalid_rating": "Initial rating must be greater than zero",
        "invalid_rank": "Rank must be a positive whole number",
        "rank_label": "Rank",
        "remove_player": "Remove player",
        "manual_pair": "Manual pairing",
        "unpair": "Unpair",
        "pair_selected": "Pair selected",
        "round_results": "Round results",
        "mark_absent": "Mark absent",
        "select_round": "Select round",
        "no_rounds": "Generate a round before creating manual pairings.",
        "system_statistics": "System Statistics",
        "snapshots": "Snapshots",
        "glicko_formula": "Glicko-2 Formula",
        "rating_parameters": "Rating Parameters",
        "tau_label": "TAU",
        "default_rating_label": "Default Rating",
        "default_rd_label": "Default RD",
        "default_volatility_label": "Default Volatility",
        "save_parameters": "Save Parameters",
        "reset_to_default": "Set to Default",
        "reset_to_default_success": "Values reset to defaults",
        "incremental_update": "Incremental Update",
        "ratings_out_of_date_since": "Ratings are out of date since:",
        "update_latest_snapshot": "Update From Latest Snapshot",
        "update_latest_snapshot_confirm": "Update ratings from the latest snapshot?",
        "ratings_up_to_date": "Ratings are up to date.",
        "recalculate_ratings": "Recalculate Ratings",
        "recalculate_description": "Rebuild ratings and rating history from all imported matches, starting from each player's Initial Rating.",
        "recalculate_confirm": "Recalculate all ratings and rebuild rating history?",
        "recalculate_everything": "Recalculate Everything",
        "admin_backups_title": "Backups",
        "admin_backups_desc": "Create and restore database backups.",
        "admin_users_title": "User Management",
        "admin_users_desc": "Manage accounts and roles.",
        "audit_review_heading": "Audit review",
        "audit_review_subtitle": "Review administrative actions with user and action filters.",
        "audit_review_desc": "Review administrative actions.",
        "audit_review_empty": "No audit events to display.",
        "actor_label": "User",
        "action_label": "Action",
        "resource_label": "Resource",
        "timestamp_label": "Date and time",
        "details_label": "Details",
        "all_users": "All users",
        "all_actions": "All actions",
        "audit_search_label": "Search",
        "audit_date_from": "From",
        "audit_date_to": "To",
        "user_management_heading": "User Management",
        "user_management_subtitle": "View, create, edit, and delete administrative accounts.",
        "create_user_heading": "Create user",
        "edit_user_heading": "Edit user",
        "username_label": "Username",
        "role_label": "Role",
        "timezone_label": "Time zone",
        "timezone_default": "Default (UTC-05:00)",
        "invalid_timezone": "The time zone is invalid.",
        "active_label": "Active",
        "create_user_button": "Create user",
        "edit_user_button": "Save changes",
        "delete_user_button": "Delete",
        "cancel_button": "Cancel",
        "user_created_success": "User created successfully.",
        "user_updated_success": "User updated successfully.",
        "user_deleted_success": "User deleted successfully.",
        "user_username_required": "Username is required.",
        "user_username_taken": "That username is already in use.",
        "inactive_label": "Inactive",
        "no_users": "No users available.",
        "delete_user_confirm": "Delete this user?",
        "open_link": "Open",
        "matches_heading_admin": "Match management",
        "manage_matches_subtitle": "Manage match records.",
        "add_match": "Add Match",
        "process_matches": "Process matches",
        "pending_new_players": "New users pending",
        "pending_new_players_warning": "This tournament will create {count} new users when matches are processed.",
        "pending_new_players_hint": "Review the suggested matches before continuing.",
        "pending_label": "pending",
        "did_you_mean": "Did you mean",
        "no_matches_found": "No matches found.",
        "edit_player_heading": "Edit Player",
        "edit_player_subtitle": "Update player information.",
        "display_name_label": "Display Name",
        "first_name_label": "First Name",
        "last_name_label": "Last Name",
        "slug_label": "Slug",
        "save_changes": "Save Changes",
        "ratings_heading": "Ratings",
        "refresh_stats_title": "Refresh Statistics",
        "refresh_stats_desc": "Recalculate wins, losses and games played.",
        "refresh_button": "Refresh",
        "new": "New",
        "preview": "Preview",
        "admin_categories_preview_desc": "Preview category changes before saving. Sample includes the 5 highest-rated players and random active players.",
        "recalculate_ratings_title": "Recalculate Ratings",
        "recalculate_ratings_desc": "Rebuild ratings from match history.",
        "recalculate_button": "Recalculate",
        "rebuild_everything_title": "Rebuild Everything",
        "rebuild_everything_desc": "Full rebuild of ratings and statistics.",
        "rebuild_button": "Rebuild",
        "backups_heading": "Backups",
        "backups_subtitle": "Create and restore database backups.",
        "create_backup_button": "Create backup",
        "name_label": "Name",
        "page_size_label": "Page size",
        "glicko_range_label": "Glicko range",
        "last_active": "Last active",
        "any_time": "Any time",
        "last_30_days": "Last 30 days",
        "last_90_days": "Last 90 days",
        "last_year": "Last year",
        "page_label": "Page",
        "showing_results": "Showing results",
        "of_label": "of",
        "prev_page": "Prev",
        "next_page": "Next",
        "actions_label": "Actions",
        "no_backups": "No backups available.",
        "category_converter_heading": "Glicko → Category",
        "glicko_input_placeholder": "Enter Glicko rating",
        "glicko_input_label": "Glicko Rating",
        "clear_search": "Clear",
        "search": "Search",
        "sort_label": "Sort by",
        "order_label": "Order",
        "desc_label": "Descending",
        "asc_label": "Ascending",
        "convert_button": "Convert",
        "add_match_heading": "Add Match",
        "edit_match_heading": "Edit Match",
        "match_date_label": "Date",
        "white_player_label": "White",
        "black_player_label": "Black",
        "result_label": "Result",
        "event_label": "Event",
        "notes_label": "Notes",
        "select_player": "Select a player",
        "white_wins": "White wins (1-0)",
        "black_wins": "Black wins (0-1)",
        "draw_result": "Draw (1/2-1/2)",
        "save_match": "Save Match",
        "delete_match_confirmation": "Delete this match? This action cannot be undone.",
        "same_player_error": "White and Black must be different players",
        "footer": "dbACG 2026",
        "contact": "Contact",
        "report_results": "Report results",
        "report_results_help": "Tournament organizers can report results",
        "reports_title": "Reports",
        "export_report": "Export",
        "export_report_pdf": "Export PDF",
        "export_report_csv": "Export CSV",
        "report_period": "Period",
        "report_start_date": "Start date",
        "report_end_date": "End date",
        "report_player": "Player",
        "report_player_export": "Export a player report",
        "all_players": "All players",
        "apply_filters": "Apply",
        "period_month": "This month",
        "period_custom": "Custom",
        "report_players": "Player performance",
        "matches_total": "Matches",
        "rating_change_points": "Rating points",
        "rating_change_percentage": "Rating %",
        "category_change": "Category change",
        "report_countries": "Performance by opponent country",
        "report_clubs": "Performance by opponent club",
        "report_empty": "No games in this period.",
        "report_excluded": "Excluded records",
        "country": "Country",
        "club": "Club",
        "here": "here",
        "no_rating_history": "No rating history since 2020.",
        "export_results": "Export results",
        "completed": "Completed",
        "active": "Ongoing",
        "ongoing": "Ongoing",
        "canceled": "Canceled",
        "draft": "Draft"
    },
    "pt": {
        "title": "bdACG",
        "subtitle": "Associação Colombiana de Go",
        "database": "Banco de dados da ",
        "home": "Início",
        "rankings": "Classificação",
        "players": "Jogadores",
        "import": "Importar",
        "language": "Idioma",
        "english": "English",
        "spanish": "Español",
        "portuguese": "Português",
        "team": "Equipe",
        "news": "Notícias",
        "hero_title": "Portal de classificações",
        "hero_text": "Este portal ajuda a gerenciar o banco de dados de jogadores, partidas e classificações da comunidade.",
        "hero_text2": "Os jogadores podem consultar seus perfis e ver seu histórico de partidas.",
        "cta_rankings": "Abrir classificação",
        "cta_players": "Ver jogadores",
        "stats_title": "Estatísticas da comunidade",
        "stats": [
            "Classificações de todos os tempos, anuais e trimestrais",
            "Jogadores mais ativos e maiores ganhos de Glicko",
            "Vitórias com as brancas e com as pretas"
        ],
        "stats_badge_top_rated": "Melhor rating",
        "stats_period_all_time": "Todo o período",
        "stats_period_year": "Ano atual",
        "stats_period_quarter": "Trimestre atual",
        "stats_metric_active": "Mais ativo",
        "stats_metric_wins": "Mais vitórias",
        "stats_metric_glicko": "Maior evolução",
        "stats_metric_white": "Mais vitórias com as brancas",
        "stats_metric_black": "Mais vitórias com as pretas",
        "stats_empty": "Ainda não há partidas neste período.",
        "rankings_heading": "Classificação atual",
        "rankings_subtitle": "Ordenada pelo rating Glicko atual",
        "table_heading": "Tabela de resultados",
        "position": "Posição",
        "player": "Jogador",
        "rating": "Rating",
        "category": "Categoria",
        "category_nav": "Categoria",
        "opponent": "Adversário",
        "opponent_records": "Resultados contra adversários",
        "rd": "RD",
        "games": "Partidas",
        "recent_form": "Forma recente",
        "recent_change": "Mudança recente",
        "recent_results": "Resultados recentes",
        "total_results": "Resultados totais",
        "yearly_results": "Resultados anuais",
        "quarterly_results": "Resultados trimestrais",
        "change": "Mudança",
        "overall_results": "Geral",
        "win_rate": "Percentual de vitórias",
        "recent_streak": "Última sequência",
        "career_milestones": "Marcos",
        "best_rating": "Melhor rating",
        "last_played": "Última partida",
        "streak_count": "Maior sequência",
        "as_white": "Com as brancas",
        "as_black": "Com as pretas",
        "source_note": "Apenas jogadores ativos são exibidos",
        "players_heading": "Diretório de jogadores",
        "players_subtitle": "Pesquise e consulte perfis de jogadores",
        "profile_heading": "Perfil do jogador",
        "profile_subtitle": "Rating atual, estatísticas e histórico de partidas",
        "season": "Temporada",
        "all_seasons": "Todas as temporadas",
        "all_categories": "Todas as categorias",
        "current_streak": "Sequência atual de vitórias",
        "tournament_overview": "Resumo dos Torneios",
        "no_tournament_history": "Nenhum histórico de torneios.",
        "seed_rank": "Cabeça de chave",
        "final_position": "Posição final",
        "current_rating": "Rating atual",
        "games_played": "Partidas jogadas",
        "wins": "Vitórias",
        "losses": "Derrotas",
        "draws": "Empates",
        "win_pct": "% de vitórias",
        "match_history": "Histórico de partidas",
        "date": "Data",
        "time_round": "Rodada",
        "white": "Brancas",
        "black": "Pretas",
        "winner": "Vencedor",
        "event": "Evento",
        "result": "Resultado",
        "result_win": "Vitória",
        "result_loss": "Derrota",
        "result_draw": "Empate",
        "chart_title": "Histórico de rating",
        "chart_caption": "O gráfico mostra o rating do jogador ao longo do tempo conforme as partidas são importadas.",
        "baseline": "Base 1500",
        "best_rating": "Melhor rating",
        "player_badges_heading": "Conquistas",
        "country_colombia": "Colômbia",
        "badge_rank_prefix": "#",
        "glicko_scale": "Escala Glicko",
        "glicko_label": "Glicko",
        "formula": "Fórmula",
        "category_parameters": "Parâmetros do Glicko",
        "constant": "constante",
        "minimum": "mínimo",
        "dan_label": "dan",
        "kyu_label": "kyu",
        "required_columns_missing": "Faltam colunas obrigatórias",
        "unsupported_file_format": "Formato de arquivo não suportado",
        "invalid_date_format": "Formato de data inválido (use AAAA-MM-DD)",
        "period_all_time": "Todo o período",
        "period_year": "Este ano",
        "period_quarter": "Este trimestre",
        "period_current": "Atual",
        "edit_heading": "Editar",
        "delete_heading": "Excluir",
        "import_heading": "Importar partidas",
        "import_subtitle": "Excel (CSV ou XLSX) e OpenGotha (XML).",
        "upload_file": "Enviar arquivo",
        "submit": "Importar",
        "delete_confirmation": "Excluir este jogador e todas as partidas relacionadas? Esta ação não pode ser desfeita.",
        "success": "Alteração concluída",
        "error": "Falha na alteração",
        "no_file": "Escolha um arquivo",
        "edit_subtitle": "Atualize o rating, o RD e a volatilidade",
        "admin_login_heading": "Login do administrador",
        "password_label": "Senha",
        "login_button": "Entrar",
        "logout_button": "Sair",
        "profile_title": "Meu perfil",
        "email_label": "E-mail",
        "language_label": "Idioma padrão",
        "language_names": {"es": "Espanhol", "en": "Inglês", "pt": "Português"},
        "theme_label": "Tema",
        "theme_names": {"light": "Claro", "dark": "Escuro"},
        "change_password_heading": "Alterar senha",
        "current_password_label": "Senha atual",
        "new_password_label": "Nova senha",
        "confirm_password_label": "Confirmar senha",
        "save_profile_button": "Salvar perfil",
        "profile_updated_success": "Perfil atualizado",
        "current_password_invalid": "A senha atual está incorreta",
        "password_too_short": "A senha deve ter pelo menos 8 caracteres",
        "password_mismatch": "As senhas não coincidem",
        "invalid_email": "O e-mail não é válido",
        "email_taken": "Esse e-mail já está em uso",
        "invalid_language": "O idioma não é válido",
        "invalid_theme": "O tema não é válido",
        "forgot_password_link": "Esqueceu sua senha?",
        "forgot_password_title": "Recuperar senha",
        "send_reset_button": "Enviar link",
        "password_reset_requested": "Se existir uma conta com esse e-mail, ela receberá um link de recuperação.",
        "reset_password_title": "Definir nova senha",
        "reset_password_button": "Alterar senha",
        "password_reset_success": "Senha atualizada",
        "invalid_reset_token": "O link não é válido ou expirou",
        "invalid_password": "Senha inválida",
        "welcome_user": "Bem-vindo",
        "admin_menu_link": "Menu de administração",
        "admin_heading": "Administração",
        "admin_tournament_operations_heading": "Operações de torneios",
        "admin_data_management_heading": "Gerenciamento de dados",
        "admin_management_heading": "Administração e acesso",
        "admin_settings_title": "Configurações da aplicação",
        "admin_settings_desc": "Ajuste os limites de acesso e recuperação de contas.",
        "security_settings_heading": "Segurança e recuperação",
        "max_login_attempts_label": "Tentativas de login permitidas",
        "login_window_seconds_label": "Janela de login (segundos)",
        "password_reset_ttl_seconds_label": "Duração do link de recuperação (segundos)",
        "invalid_application_settings": "As configurações não são válidas",
        "back_to_admin_index": "Voltar ao índice de administração",
        "back_to_tournament": "Voltar ao torneio",
        "back_to_tournament_list": "Voltar à lista de torneios",
        "admin_categories_title": "Categorias",
        "admin_categories_desc": "Configure a conversão de categorias.",
        "admin_import_title": "Importação",
        "admin_import_desc": "Importe arquivos Excel, CSV e OpenGotha.",
        "admin_matches_title": "Partidas",
        "admin_matches_desc": "Consulte e gerencie partidas.",
        "admin_players_title": "Jogadores",
        "admin_players_desc": "Filtre e gerencie jogadores.",
        "admin_ratings_title": "Ratings",
        "admin_ratings_desc": "Configure e recalcule ratings.",
        "tournaments_title": "Torneios",
        "public_tournaments_heading": "Torneios públicos",
        "public_tournaments_subtitle": "Consulte emparelhamentos, resultados por rodada e classificação.",
        "no_public_tournaments": "Não há torneios públicos disponíveis.",
        "show_drafts": "Mostrar rascunhos",
        "hide_drafts": "Ocultar rascunhos",
        "tournaments_desc": "Crie torneios e gerencie emparelhamentos.",
        "tournament_name": "Nome do torneio",
        "tournament_description": "Descrição do torneio",
        "settings": "Configurações",
        "pairing_system": "Sistema de emparelhamento",
        "tournament_type": "Tipo de torneio",
        "bye_points": "Pontos por folga",
        "absent_points": "Pontos por ausência",
        "handicap_stones_label": "Pedras de handicap",
        "handicap_tournament_label": "Handicap",
        "acceleration_categories": "Categorias limite de aceleração",
        "acceleration_category_count": "Número de categorias limite",
        "acceleration_category_floor": "Limite inferior",
        "acceleration_rounds": "Rodadas com aceleração",
        "category_sections": "Seções por categoria",
        "category_rounds": "Rodadas com seções estritas",
        "handicap_status_enabled": "Sim",
        "handicap_status_disabled": "Não",
        "handicap_apply_existing_confirmation": "Aplicar o handicap automático aos emparelhamentos existentes? Se escolher Não, eles serão definidos como 0.",
        "standings": "Classificação",
        "score": "Pontuação",
        "mms": "MMS",
        "sos": "SOS",
        "sosos": "SOSOS",
        "sodos": "SODOS",
        "swiss": "Suíço",
        "swiss_cat": "Suíço por categoria",
        "accelerated_swiss": "Suíço acelerado",
        "mcmahon": "McMahon",
        "rounds": "Rodadas",
        "location": "Local",
        "create_tournament": "Criar torneio",
        "new_player_option": "Criar jogador novo",
        "set_player": "Definir jogador",
        "import_opengotha": "Importar XML do OpenGotha",
        "tournament_file": "Arquivo XML do torneio",
        "generate_round": "Gerar próxima rodada",
        "tournament_pairings": "Emparelhamentos do torneio",
        "tournament_round": "Rodada",
        "board": "Mesa",
        "bye": "Folga",
        "absent": "Ausente",
        "status": "Status",
        "no_tournaments": "Nenhum torneio encontrado.",
        "unpaired_players": "Jogadores sem emparelhamento",
        "paired_tables": "Mesas emparelhadas",
        "no_unpaired_players": "Todos os jogadores estão emparelhados nesta rodada.",
        "no_paired_tables": "Não há mesas emparelhadas nesta rodada.",
        "delete_tournament_heading": "Excluir torneio",
        "delete_tournament_confirmation": "Excluir este torneio e todas as suas rodadas, emparelhamentos e jogadores? Esta ação não pode ser desfeita.",
        "cancel_button": "Cancelar",
        "participant_list": "Lista de jogadores",
        "player_management": "Gerenciar jogadores",
        "add_player": "Adicionar jogador",
        "create_player": "Criar jogador",
        "player_created": "Jogador criado e adicionado ao torneio",
        "create_pending_player": "Criar jogador pendente",
        "pending_player_created": "Jogador pendente criado para este torneio",
        "player_already_exists": "Já existe um jogador com este nome: {name}",
        "similar_player_exists": "Já existe um jogador com um nome semelhante: {name}",
        "pending_player_deleted": "Jogador pendente excluído",
        "pending_player_delete_confirmation": "Excluir este jogador pendente? Esta ação não pode ser desfeita.",
        "player_name_required": "O nome do jogador é obrigatório",
        "invalid_rating": "O rating inicial deve ser maior que zero",
        "invalid_rank": "A classificação deve ser um número inteiro maior que zero",
        "rank_label": "Classificação",
        "remove_player": "Remover jogador",
        "manual_pair": "Emparelhamento manual",
        "unpair": "Desemparelhar",
        "pair_selected": "Emparelhar selecionados",
        "round_results": "Resultados da rodada",
        "mark_absent": "Marcar ausente",
        "select_round": "Selecionar rodada",
        "unpair": "Desemparelhar",
        "no_rounds": "Gere uma rodada antes de criar emparelhamentos manuais.",
        "system_statistics": "Estatísticas do sistema",
        "snapshots": "Capturas",
        "glicko_formula": "Fórmula Glicko-2",
        "rating_parameters": "Parâmetros de rating",
        "tau_label": "TAU",
        "default_rating_label": "Rating padrão",
        "default_rd_label": "RD padrão",
        "default_volatility_label": "Volatilidade padrão",
        "save_parameters": "Salvar parâmetros",
        "reset_to_default": "Restaurar padrões",
        "reset_to_default_success": "Valores restaurados para os padrões",
        "incremental_update": "Atualização incremental",
        "ratings_out_of_date_since": "Os ratings estão desatualizados desde:",
        "update_latest_snapshot": "Atualizar a partir do último snapshot",
        "update_latest_snapshot_confirm": "Atualizar os ratings a partir do último snapshot?",
        "ratings_up_to_date": "Os ratings estão atualizados.",
        "recalculate_ratings": "Recalcular ratings",
        "recalculate_description": "Reconstrua os ratings e o histórico a partir de todas as partidas importadas, começando pelo rating inicial de cada jogador.",
        "recalculate_confirm": "Recalcular todos os ratings e reconstruir o histórico?",
        "recalculate_everything": "Recalcular tudo",
        "admin_backups_title": "Backups",
        "admin_backups_desc": "Crie e restaure cópias de segurança.",
        "admin_users_title": "Gerenciamento de usuários",
        "admin_users_desc": "Gerencie contas e funções.",
        "audit_review_heading": "Revisão de auditoria",
        "audit_review_subtitle": "Revise ações administrativas com filtros por usuário e ação.",
        "audit_review_desc": "Consulte as ações administrativas.",
        "audit_review_empty": "Nenhum evento de auditoria para exibir.",
        "actor_label": "Usuário",
        "action_label": "Ação",
        "resource_label": "Recurso",
        "timestamp_label": "Data e hora",
        "details_label": "Detalhes",
        "all_users": "Todos os usuários",
        "all_actions": "Todas as ações",
        "audit_search_label": "Buscar",
        "audit_date_from": "De",
        "audit_date_to": "Até",
        "user_management_heading": "Gerenciamento de usuários",
        "user_management_subtitle": "Visualize, crie, edite e exclua contas administrativas.",
        "create_user_heading": "Criar usuário",
        "edit_user_heading": "Editar usuário",
        "username_label": "Usuário",
        "role_label": "Função",
        "timezone_label": "Fuso horário",
        "timezone_default": "Padrão (UTC-05:00)",
        "invalid_timezone": "O fuso horário é inválido.",
        "active_label": "Ativo",
        "create_user_button": "Criar usuário",
        "edit_user_button": "Salvar alterações",
        "delete_user_button": "Excluir",
        "cancel_button": "Cancelar",
        "user_created_success": "Usuário criado com sucesso.",
        "user_updated_success": "Usuário atualizado com sucesso.",
        "user_deleted_success": "Usuário excluído com sucesso.",
        "user_username_required": "O nome de usuário é obrigatório.",
        "user_username_taken": "Esse nome de usuário já está em uso.",
        "inactive_label": "Inativo",
        "no_users": "Nenhum usuário disponível.",
        "delete_user_confirm": "Excluir este usuário?",
        "open_link": "Abrir",
        "matches_heading_admin": "Gerenciamento de partidas",
        "manage_matches_subtitle": "Gerencie os registros de partidas.",
        "add_match": "Adicionar partida",
        "process_matches": "Processar partidas",
        "pending_new_players": "Novos jogadores pendentes",
        "pending_new_players_warning": "Este torneio criará {count} novos utilizadores ao processar as partidas.",
        "pending_new_players_hint": "Revise os nomes sugeridos antes de continuar.",
        "pending_label": "pendente",
        "did_you_mean": "Quiseste dizer",
        "no_matches_found": "Nenhuma partida encontrada.",
        "edit_player_heading": "Editar jogador",
        "edit_player_subtitle": "Atualize as informações do jogador.",
        "display_name_label": "Nome de exibição",
        "first_name_label": "Nome",
        "last_name_label": "Sobrenome",
        "slug_label": "Slug",
        "save_changes": "Salvar alterações",
        "ratings_heading": "Ratings",
        "refresh_stats_title": "Atualizar estatísticas",
        "refresh_stats_desc": "Recalcule vitórias, derrotas e partidas jogadas.",
        "refresh_button": "Atualizar",
        "new": "Novo",
        "preview": "Pré-visualização",
        "admin_categories_preview_desc": "Visualize as alterações de categoria antes de salvar. A amostra inclui os 5 jogadores com maior rating e jogadores ativos aleatórios.",
        "recalculate_ratings_title": "Recalcular ratings",
        "recalculate_ratings_desc": "Reconstrua os ratings a partir do histórico de partidas.",
        "recalculate_button": "Recalcular",
        "rebuild_everything_title": "Reconstruir tudo",
        "rebuild_everything_desc": "Reconstrução completa de ratings e estatísticas.",
        "rebuild_button": "Reconstruir",
        "backups_heading": "Backups",
        "backups_subtitle": "Crie e restaure backups do banco de dados.",
        "create_backup_button": "Criar backup",
        "name_label": "Nome",
        "page_size_label": "Resultados por página",
        "glicko_range_label": "Faixa Glicko",
        "last_active": "Última atividade",
        "any_time": "Qualquer momento",
        "last_30_days": "Últimos 30 dias",
        "last_90_days": "Últimos 90 dias",
        "last_year": "Último ano",
        "page_label": "Página",
        "showing_results": "Mostrando resultados",
        "of_label": "de",
        "prev_page": "Anterior",
        "next_page": "Próxima",
        "actions_label": "Ações",
        "no_backups": "Nenhum backup disponível.",
        "category_converter_heading": "Glicko → Categoria",
        "glicko_input_placeholder": "Digite o rating Glicko",
        "glicko_input_label": "Rating Glicko",
        "clear_search": "Limpar",
        "search": "Pesquisar",
        "sort_label": "Ordenar por",
        "order_label": "Ordem",
        "desc_label": "Decrescente",
        "asc_label": "Crescente",
        "convert_button": "Converter",
        "add_match_heading": "Adicionar partida",
        "edit_match_heading": "Editar partida",
        "match_date_label": "Data",
        "white_player_label": "Brancas",
        "black_player_label": "Pretas",
        "result_label": "Resultado",
        "event_label": "Evento",
        "notes_label": "Observações",
        "select_player": "Selecione um jogador",
        "white_wins": "Vitória das brancas (1-0)",
        "black_wins": "Vitória das pretas (0-1)",
        "draw_result": "Empate (1/2-1/2)",
        "save_match": "Salvar partida",
        "delete_match_confirmation": "Excluir esta partida? Esta ação não pode ser desfeita.",
        "same_player_error": "Brancas e pretas devem ser jogadores diferentes",
        "footer": "dbACG 2026",
        "contact": "Contato",
        "report_results": "Reportar resultados",
        "report_results_help": "Se você organiza torneios, pode reportar os resultados",
        "reports_title": "Relatórios",
        "export_report": "Exportar",
        "export_report_pdf": "Exportar PDF",
        "export_report_csv": "Exportar CSV",
        "report_period": "Período",
        "report_start_date": "Data inicial",
        "report_end_date": "Data final",
        "report_player": "Jogador",
        "report_player_export": "Exportar relatório do jogador",
        "all_players": "Todos os jogadores",
        "apply_filters": "Aplicar",
        "period_month": "Este mês",
        "period_custom": "Personalizado",
        "report_players": "Desempenho por jogador",
        "matches_total": "Partidas",
        "rating_change_points": "Pontos de rating",
        "rating_change_percentage": "% de rating",
        "category_change": "Mudança de categoria",
        "report_countries": "Desempenho por país do oponente",
        "report_clubs": "Desempenho por clube do oponente",
        "report_empty": "Não há jogos neste período.",
        "report_excluded": "Registros excluídos",
        "country": "País",
        "club": "Clube",
        "here": "aqui",
        "no_rating_history": "Não há histórico de rating desde 2020.",
        "export_results": "Exportar resultados",
        "completed": "Concluído",
        "active": "Em andamento",
        "ongoing": "Em andamento",
        "canceled": "Cancelado",
        "draft": "Rascunho"
    }
}

def get_language(value):
    if value in TRANSLATIONS:
        return value
    if value in (None, ""):
        try:
            value = session.get("user_language")
        except RuntimeError:
            value = None
        if value in (None, ""):
            try:
                value = request.cookies.get("user_language")
            except RuntimeError:
                value = None
    return value if value in TRANSLATIONS else "es"


def validate_theme(value):
    if value not in {"light", "dark"}:
        raise ValueError("unsupported theme")
    return value


def validate_email_address(value):
    value = (value or "").strip().lower()
    if not value or "@" not in value or value.startswith("@") or value.endswith("@"):
        raise ValueError("invalid email")
    local_part, domain = value.rsplit("@", 1)
    if not local_part or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("invalid email")
    return value


def migrate_auth_schema(conn):
    """Create the additive auth tables and seed the three built-in roles."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE CHECK(name IN ('administrator', 'tournament_director', 'operator')),
            description TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS roles_validate_insert
        BEFORE INSERT ON roles
        BEGIN
            SELECT CASE
                WHEN NEW.name NOT IN ('administrator', 'tournament_director', 'operator')
                THEN RAISE(ABORT, 'role name not allowed')
            END;
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS roles_validate_update
        BEFORE UPDATE ON roles
        BEGIN
            SELECT CASE
                WHEN NEW.name NOT IN ('administrator', 'tournament_director', 'operator')
                THEN RAISE(ABORT, 'role name not allowed')
            END;
        END;
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_login_at TEXT,
            timezone TEXT,
            email TEXT,
            language TEXT DEFAULT 'es',
            theme TEXT DEFAULT 'light'
        )
        """
    )
    user_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    for column_name in ("timezone", "email", "language", "theme"):
        if column_name not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column_name} TEXT")
    conn.execute("UPDATE users SET language = 'es' WHERE language IS NULL")
    conn.execute("UPDATE users SET theme = 'light' WHERE theme IS NULL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            UNIQUE(user_id, role_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user ON password_reset_tokens (user_id, created_at DESC)"
    )

    for role_name in ("administrator", "tournament_director", "operator"):
        conn.execute(
            "INSERT OR IGNORE INTO roles (name, description) VALUES (?, ?)",
            (role_name, role_name),
        )

    conn.commit()


def bootstrap_default_admin_account(conn=None, password=None):
    """Create the one-time default admin account when configured via env/password."""
    conn = conn or get_db()
    password = password or os.environ["ADMIN_PASSWORD"]
    if not password:
        return None

    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count > 0:
        return None

    username = "admin"
    existing = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if existing is not None:
        return existing[0]

    user_id = conn.execute(
        "INSERT INTO users (username, password_hash, is_active, created_at) VALUES (?, ?, 1, ?)",
        (username, generate_password_hash(password), current_timestamp()),
    ).lastrowid

    role_id = conn.execute(
        "SELECT id FROM roles WHERE name = 'administrator'"
    ).fetchone()
    if role_id is not None:
        conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (user_id, role_id[0]),
        )

    conn.commit()
    return user_id


def create_user_account(
    username,
    password,
    role_name="operator",
    timezone_name=None,
    conn=None,
    email=None,
):
    """Create a named user account with a hashed password and role assignment."""
    if not isinstance(username, str) or not username.strip():
        raise ValueError("username is required")
    if not isinstance(password, str) or not password:
        raise ValueError("password is required")

    normalized_username = username.strip()
    role_name = (role_name or "operator").strip()
    if role_name not in ALLOWED_ROLES:
        raise ValueError("unsupported role")
    timezone_name = validate_timezone(timezone_name)
    email = validate_email_address(email) if email else None

    owns_connection = conn is None
    conn = conn or get_db()
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row

    try:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (normalized_username,)).fetchone():
            raise ValueError("username already exists")
        if email and conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise ValueError("email already exists")

        role = conn.execute("SELECT id FROM roles WHERE name = ?", (role_name,)).fetchone()
        if role is None:
            raise ValueError("role not found")

        user_id = conn.execute(
            "INSERT INTO users (username, password_hash, is_active, created_at, timezone, email, language, theme) VALUES (?, ?, 1, ?, ?, ?, 'es', 'light')",
            (normalized_username, generate_password_hash(password), current_timestamp(), timezone_name, email),
        ).lastrowid
        conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (user_id, role["id"]),
        )
        conn.commit()
        return user_id
    finally:
        if owns_connection:
            conn.close()


def get_current_user(conn=None):
    user_id = session.get("user_id")
    if user_id is None:
        return None

    owns_connection = conn is None
    conn = conn or get_db()
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row

    try:
        users_table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if users_table_exists is None:
            session.clear()
            return None

        user = conn.execute(
            """
                 SELECT u.id, u.username, u.password_hash, u.is_active,
                     u.email, u.language, u.theme, u.timezone
            FROM users u
            WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
        if user is None or user["is_active"] != 1:
            session.clear()
            return None

        role = conn.execute(
            """
            SELECT r.name
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = ?
            ORDER BY r.name
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        payload = dict(user)
        payload["role"] = role["name"] if role else None
        return payload
    finally:
        if owns_connection:
            conn.close()


def user_has_permission(permission_name):
    user = get_current_user()
    if user is None:
        return False

    role = user.get("role")
    if permission_name == "admin":
        return role == "administrator"
    if permission_name == "tournament_admin":
        return role in {"administrator", "tournament_director"}
    if permission_name == "data_admin":
        return role in {"administrator", "operator"}
    if permission_name == "operator":
        return role in {"administrator", "tournament_director", "operator"}
    if permission_name == "dashboard":
        return role in {"administrator", "tournament_director", "operator"}
    return role == "administrator"


def authenticate_user(username, password, conn=None):
    owns_connection = conn is None
    conn = conn or get_db()
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row

    try:
        users_table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if users_table_exists is None:
            return None

        user = conn.execute(
            "SELECT id, username, password_hash, is_active, email, language, theme, timezone FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if user is None or user["is_active"] != 1:
            return None
        if check_password_hash(user["password_hash"], password):
            role = conn.execute(
                """
                SELECT r.name
                FROM user_roles ur
                JOIN roles r ON r.id = ur.role_id
                WHERE ur.user_id = ?
                ORDER BY r.name
                LIMIT 1
                """,
                (user["id"],),
            ).fetchone()
            payload = dict(user)
            payload["role"] = role["name"] if role else None
            return payload
        return None
    finally:
        if owns_connection:
            conn.close()


def _utc_timestamp(value):
    return value.astimezone(datetime_timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def create_password_reset_token(user_id, conn=None):
    """Create a single-use password reset token and return its raw value."""
    from services.settings_service import get_application_settings

    owns_connection = conn is None
    conn = conn or get_db()
    try:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now = datetime.now(datetime_timezone.utc)
        reset_ttl = get_application_settings(conn=conn)["password_reset_ttl_seconds"]
        expires_at = now + timedelta(seconds=reset_ttl)
        conn.execute(
            "DELETE FROM password_reset_tokens WHERE user_id = ? AND used_at IS NULL",
            (user_id,),
        )
        conn.execute(
            """
            INSERT INTO password_reset_tokens
                (user_id, token_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, token_hash, _utc_timestamp(expires_at), _utc_timestamp(now)),
        )
        if owns_connection:
            conn.commit()
        return raw_token
    finally:
        if owns_connection:
            conn.close()


def reset_password_with_token(raw_token, new_password, conn=None):
    """Consume a valid reset token and replace the account password."""
    if not isinstance(new_password, str) or len(new_password) < 8:
        raise ValueError("password too short")
    owns_connection = conn is None
    conn = conn or get_db()
    try:
        token_hash = hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()
        now = _utc_timestamp(datetime.now(datetime_timezone.utc))
        row = conn.execute(
            """
            SELECT prt.id
            FROM password_reset_tokens prt
            JOIN users u ON u.id = prt.user_id
            WHERE prt.token_hash = ?
              AND prt.used_at IS NULL
              AND prt.expires_at > ?
              AND u.is_active = 1
            """,
            (token_hash, now),
        ).fetchone()
        if row is None:
            return False
        token_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = (SELECT user_id FROM password_reset_tokens WHERE id = ?)",
            (generate_password_hash(new_password), token_id),
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
            (now, token_id),
        )
        if owns_connection:
            conn.commit()
        return True
    finally:
        if owns_connection:
            conn.close()


def send_password_reset_email(recipient, reset_url):
    """Send a password reset email using the configured SMTP server."""
    from services.settings_service import get_application_settings

    if not MAIL_SERVER:
        raise RuntimeError("password reset email is not configured")
    reset_ttl = get_application_settings()["password_reset_ttl_seconds"]
    message = EmailMessage()
    message["Subject"] = "Password reset"
    message["From"] = MAIL_FROM or MAIL_USERNAME or "no-reply@localhost"
    message["To"] = recipient
    message.set_content(
        "Use this link to set a new password for your account:\n\n"
        f"{reset_url}\n\n"
        f"This link expires in {max(1, reset_ttl // 60)} minutes."
    )
    with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10) as smtp:
        if MAIL_USE_TLS:
            smtp.starttls()
        if MAIL_USERNAME:
            smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
        smtp.send_message(message)


def admin_required(permission_name="operator"):
    user = get_current_user()
    if user is None:
        return False

    return user_has_permission(permission_name)


def migrate_audit_log_schema(conn):
    """Create the admin audit log table and indexes if they are missing."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action_type TEXT NOT NULL,
            resource_type TEXT,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_user_time ON audit_log (user_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_action_time ON audit_log (action_type, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_time ON audit_log (created_at DESC)"
    )
    conn.execute(
        "DELETE FROM audit_log WHERE created_at < ?",
        (timestamp_days_ago(AUDIT_RETENTION_DAYS),),
    )
    conn.commit()


def log_admin_action(action_type, resource_type=None, details=None, user_id=None, conn=None):
    """Persist an authenticated admin action for audit review."""
    owns_connection = conn is None
    conn = conn or get_db()

    try:
        migrate_audit_log_schema(conn)

        if user_id is None:
            try:
                user_id = session.get("user_id")
            except RuntimeError:
                user_id = None

        if details is None:
            encoded_details = "{}"
        elif isinstance(details, (dict, list, tuple)):
            encoded_details = json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)
        else:
            encoded_details = json.dumps(str(details), ensure_ascii=False)

        encoded_details = encoded_details.encode("utf-8")[:AUDIT_DETAILS_MAX_BYTES].decode(
            "utf-8", errors="ignore"
        )

        conn.execute(
            """
            INSERT INTO audit_log (user_id, action_type, resource_type, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, action_type, resource_type, encoded_details, current_timestamp()),
        )
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def refresh_stats(conn=None):
    owns_connection = conn is None
    conn = conn or get_db()
    try:
        conn.execute(
        """
        WITH player_results AS (
            SELECT
                white_player_id AS player_id,
                1 AS games,
                CASE WHEN result = '1-0' THEN 1 ELSE 0 END AS wins,
                CASE WHEN result = '0-1' THEN 1 ELSE 0 END AS losses,
                CASE WHEN result NOT IN ('1-0', '0-1') THEN 1 ELSE 0 END AS draws
            FROM matches
            UNION ALL
            SELECT
                black_player_id AS player_id,
                1 AS games,
                CASE WHEN result = '0-1' THEN 1 ELSE 0 END AS wins,
                CASE WHEN result = '1-0' THEN 1 ELSE 0 END AS losses,
                CASE WHEN result NOT IN ('1-0', '0-1') THEN 1 ELSE 0 END AS draws
            FROM matches
        ),
        totals AS (
            SELECT player_id, SUM(games) AS games, SUM(wins) AS wins,
                   SUM(losses) AS losses, SUM(draws) AS draws
            FROM player_results
            GROUP BY player_id
        )
        UPDATE players
        SET
            games_played = COALESCE((SELECT games FROM totals WHERE totals.player_id = players.id), 0),
            wins = COALESCE((SELECT wins FROM totals WHERE totals.player_id = players.id), 0),
            losses = COALESCE((SELECT losses FROM totals WHERE totals.player_id = players.id), 0),
            draws = COALESCE((SELECT draws FROM totals WHERE totals.player_id = players.id), 0)
        """
        )
        conn.commit()
    finally:
        if owns_connection:
            conn.close()


def build_smooth_path(points):
    if not points:
        return ""
    if len(points) == 1:
        return f"M {points[0]['x']:.2f},{points[0]['y']:.2f}"
    if len(points) == 2:
        return f"M {points[0]['x']:.2f},{points[0]['y']:.2f} L {points[1]['x']:.2f},{points[1]['y']:.2f}"

    parts = [f"M {points[0]['x']:.2f},{points[0]['y']:.2f}"]
    for index in range(1, len(points)):
        prev = points[index - 1]
        current = points[index]
        if index == len(points) - 1:
            cp1x = prev["x"] + (current["x"] - prev["x"]) * 0.5
            cp1y = prev["y"]
            cp2x = current["x"] - (current["x"] - prev["x"]) * 0.5
            cp2y = current["y"]
        else:
            next_point = points[index + 1]
            cp1x = prev["x"] + (current["x"] - prev["x"]) * 0.5
            cp1y = prev["y"]
            cp2x = current["x"] - (next_point["x"] - prev["x"]) * 0.25
            cp2y = current["y"]
        parts.append(f"C {cp1x:.2f},{cp1y:.2f} {cp2x:.2f},{cp2y:.2f} {current['x']:.2f},{current['y']:.2f}")
    return " ".join(parts)


def build_rating_chart_data(snapshots):
    if not snapshots:
        return {
            "points": [],
            "polyline": "",
            "path": "",
            "baseline_y": 0,
            "min_rating": 0,
            "max_rating": 0,
            "label_min": 0,
            "label_max": 0,
        }

    ratings = [row["rating"] for row in snapshots]

    initial_rating = snapshots[0]["rating"]

    raw_min = min(ratings)
    raw_max = max(ratings)

    span = raw_max - raw_min

    if span == 0:
        padding_rating = 20
    else:
        padding_rating = max(10, span * 0.10)

    min_rating = raw_min - padding_rating
    max_rating = raw_max + padding_rating

    span = max_rating - min_rating

    padding = 24
    width = 620
    height = 220

    points = []

    for index, row in enumerate(snapshots):
        if len(snapshots) == 1:
            x = width / 2
        else:
            x = (
                padding
                + (index / (len(snapshots) - 1))
                * (width - padding * 2)
            )

        value = row["rating"]

        y = (
            height
            - padding
            - ((value - min_rating) / span)
            * (height - padding * 2)
        )

        points.append(
            {
                "x": round(x, 2),
                "y": round(y, 2),
                "date": row["snapshot_date"],
                "rating": round(value, 1),
            }
        )

    baseline_y = (
        height
        - padding
        - ((initial_rating - min_rating) / span)
        * (height - padding * 2)
    )

    label_min = round(min_rating)
    label_max = round(max_rating)

    return {
        "points": points,
        "polyline": " ".join(
            f"{point['x']},{point['y']}"
            for point in points
        ),
        "path": build_smooth_path(points),
        "baseline_y": round(baseline_y, 2),
        "min_rating": round(min_rating, 1),
        "max_rating": round(max_rating, 1),
        "label_min": label_min,
        "label_max": label_max,
        "baseline_rating": round(initial_rating, 1),
    }

