# Rutas y notas de integración

Consulta la [Ayuda de la interfaz](user_interface.md) para instrucciones orientadas a tareas y el [README en español](../README.md) para instalación y configuración.

## Tabla de contenidos

- [Estado de la API](#estado-de-la-api)
- [Rutas GET públicas](#rutas-get-públicas)
- [Autenticación y rutas de miembros](#autenticación-y-rutas-de-miembros)
- [Grupos de rutas administrativas](#grupos-de-rutas-administrativas)
- [Recomendaciones para integraciones](#recomendaciones-para-integraciones)

## Estado de la API

La aplicación actual es una aplicación web Flask renderizada en el servidor. **No publica actualmente una API REST pública estable** bajo `/api`. Las rutas `/api/backup`, `/api/player`, `/api/tournament` y otras similares del borrador anterior no están implementadas y no deben usarse para integraciones.

Las acciones administrativas son envíos de formularios HTML. Requieren una sesión autenticada, el rol correspondiente y un token CSRF válido. Algunas acciones administrativas asíncronas devuelven una pequeña respuesta JSON de redirección solo cuando la solicitud incluye `X-Requested-With: XMLHttpRequest`; esto es un comportamiento interno del navegador, no un contrato de API versionado.

## Rutas GET públicas

Estas páginas se pueden abrir sin una cuenta de administrador:

| Ruta | Uso |
| --- | --- |
| `/` | Inicio, resumen de posiciones, estadísticas y noticias publicadas |
| `/rankings` | Posiciones públicas paginadas |
| `/players` | Directorio y filtros de jugadores |
| `/player/view?id=<player_id>` | Perfil e historial de partidas de un jugador |
| `/matches` | Lista y filtros de partidas |
| `/tournaments` | Lista de torneos públicos |
| `/tournaments/<tournament_id>` | Detalles, emparejamientos y clasificación de un torneo |
| `/reports` | Reportes por periodo y jugador |
| `/reports/export.csv` | Versión CSV de los filtros del reporte |
| `/reports/export.pdf` | Versión PDF de los filtros del reporte |
| `/category` | Información de rating Glicko y categorías |
| `/sgf-library` | Biblioteca pública de SGF |
| `/sgf-library/<filename>` | Metadatos de un registro SGF |
| `/sgf/<filename>` | Descarga de un registro SGF |
| `/matches/<match_id>/record` | Visualización del SGF vinculado a una partida |
| `/matches/<match_id>/sgf` | Descarga del SGF vinculado a una partida |
| `/news/<article_id>` | Artículo de noticias publicado |

La mayoría de las páginas acepta el parámetro opcional `lang=es`, `lang=en` o `lang=pt`. Los reportes también aceptan `start_date`, `end_date` y `player_id`; consulta la guía de interfaz para más detalles.

## Autenticación y rutas de miembros

| Ruta | Método | Acceso |
| --- | --- | --- |
| `/admin/login` | GET, POST | Iniciar sesión |
| `/admin/register` | GET, POST | Crear una cuenta de miembro |
| `/admin/forgot-password` | GET, POST | Solicitar un enlace de recuperación |
| `/admin/reset-password/<token>` | GET, POST | Completar la recuperación |
| `/admin/report-results` | GET, POST | Enviar un resultado que incluya al jugador vinculado |
| `/admin/profile` | GET, POST | Gestionar el perfil de una cuenta autenticada |
| `/admin/logout` | GET | Cerrar la sesión actual |

Los envíos de miembros permanecen pendientes hasta su revisión. Antes de ser aprobados no afectan las partidas públicas, los ratings ni los reportes.

## Grupos de rutas administrativas

Todas las rutas siguientes requieren una cuenta autenticada y permisos según el rol:

- `/admin`: panel de administración
- `/admin/import`: importaciones de Excel, CSV y OpenGotha
- `/admin/backups`: crear, restaurar y eliminar copias administradas
- `/admin/players`, `/admin/matches`: gestión de jugadores y partidas
- `/admin/ratings`, `/admin/categories`: configuración de ratings y categorías
- `/admin/tournaments`: creación y gestión de torneos
- `/admin/tournaments/<tournament_id>/...`: participantes, emparejamientos, resultados, rondas y exportación
- `/admin/sgf/...`: vinculación y eliminación de SGF
- `/admin/news`: gestión de noticias
- `/admin/users`, `/admin/audit`, `/admin/settings`: cuentas, auditoría y configuración

Las rutas POST cambian el estado de la aplicación y deben usarse mediante los formularios renderizados. Las acciones de copias, restauración, importación, eliminación y torneos no están expuestas como operaciones GET sin autenticación.

## Recomendaciones para integraciones

Para una integración externa, usa las exportaciones documentadas CSV/PDF o la base de datos mediante un proceso operativo aprobado. No dependas de formularios administrativos ni de respuestas JSON no documentadas. Una futura API REST debe definir autenticación, esquemas de solicitud y respuesta, códigos de error, paginación y versionado antes de crear clientes.
