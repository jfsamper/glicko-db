# Glicko DB

Documentación: Español | [English](README.en.md) | [Português](README.pt.md)

Glicko DB es una aplicación Flask y SQLite para administrar jugadores, ratings, partidas y torneos de una communidad de Go. Ofrece posiciones y estadísticas públicas, junto con pantallas de administración protegidas para importaciones, configuración del rating, copias de seguridad y operaciones de torneos.

## Funciones

- Posiciones públicas, búsqueda de jugadores, perfiles, historial de partidas, gráficos de rating y conversión de categorías
- Cálculo Glicko-2 con parámetros de rating y categoría configurables
- Interfaz pública en español, inglés y portugués
- Administración de jugadores y partidas con paginación, filtros y ordenación consistente
- Importación de libros Excel (XLSX), OpenGotha (XML) y archivos de partidas (CSV)
- Creación y edición de torneos, importación de OpenGotha, emparejamientos, registro de resultados, clasificación y exportación
- Reportes públicos por periodo (por defecto, todo el tiempo) con filtros por jugador, exportación CSV/PDF localizada, cambios de rating y rendimiento por oponente, país y club
- Sistemas Suizo, Suizo por Categoría, Suizo Acelerado y McMahon
- Manejo de descansos y ausencias, copias de seguridad, protecciones de restauración y migraciones SQLite

## Requisitos

- Python 3.10 o posterior
- `pip`
- Paquetes de Python:
  - `Flask>=3.0`
  - `Flask-WTF>=1.2`
  - `Werkzeug>=3.0`
  - `openpyxl>=3.1`
  - `reportlab>=4.0`
  - `tzdata>=2024.1` (Windows time zone data)

## Ejecución local

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:APP_SECRET_KEY = "reemplaza-por-un-valor-aleatorio-largo"
$env:ADMIN_PASSWORD = "elige-una-contrasena-segura"
python app.py
```

macOS o Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export APP_SECRET_KEY="reemplaza-por-un-valor-aleatorio-largo"
export ADMIN_PASSWORD="elige-una-contrasena-segura"
python app.py
```

Abre `http://127.0.0.1:5000` en el navegador. La aplicación crea la base de datos SQLite en `data/acg_ratings.db` durante el primer inicio.

Solo para datos de ejemplo locales, define `LOAD_SAMPLE_DATA=1` antes de iniciar. No uses datos de ejemplo en una base de datos de producción.

## Configuración

Los valores predeterminados están en `config.py`.

- `APP_SECRET_KEY`: clave de firma de las sesiones Flask. Debe configurarse en producción.
- `ADMIN_PASSWORD`: contraseña del acceso de administración actual. Debe reemplazarse en producción.
- `LOAD_SAMPLE_DATA=1`: importa `rank-final.xlsx` si existe y reemplaza el conjunto de datos actual; solo para desarrollo local.
- `DB_PATH`: ubicación de la base de datos SQLite, definida en `config.py`.
- `AUDIT_RETENTION_DAYS`: número de días para conservar eventos de auditoría; el valor predeterminado es `730`.
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS` y `MAIL_FROM`: configuración SMTP para recuperación de contraseñas; `PASSWORD_RESET_TTL_SECONDS` controla la caducidad del enlace y usa 3600 segundos por defecto.

Las fechas y horas generadas por la aplicación usan UTC-5 por defecto. Cada cuenta puede elegir una zona horaria IANA en la gestión de usuarios; las cuentas sin preferencia mantienen UTC-5. Python usa los datos IANA del sistema en Linux y `tzdata` proporciona el respaldo portátil en Windows o en imágenes Linux mínimas. Si no se puede cargar la zona guardada de una cuenta, la presentación vuelve a UTC-5. Al calcular ratings, las partidas del mismo día se procesan por número de ronda y después por su orden de inserción; las rondas desconocidas se tratan como la ronda 1.

Los reportes de `/reports` usan rangos inclusivos `start_date` y `end_date`, y la pertenencia a un periodo se determina con la zona horaria fija del servidor (UTC-5 por defecto), no con la zona horaria de la cuenta. La vista general y los reportes filtrados por jugador comienzan en Todo el tiempo. Los porcentajes de victoria son victorias divididas por partidas. Cada fila muestra cambio absoluto de puntos, cambio porcentual y cambio entero de categoría. El selector de jugadores se ordena por número total de partidas. Los totales se calculan en el servidor una vez y se reutilizan en la vista y en las exportaciones CSV/PDF; las etiquetas y los textos del PDF siguen el idioma actual, y los registros con fecha o resultado no válidos se excluyen y se contabilizan. Las partidas materializadas desde torneos conservan una identidad única por emparejamiento para impedir importaciones o conteos duplicados.

El panel de administración usa cuentas nominadas con tres roles: `administrator`, `tournament_director` y `operator`. Si no existe ninguna cuenta, la aplicación crea un administrador inicial con la contraseña de `ADMIN_PASSWORD` durante el primer inicio; las cuentas adicionales y sus zonas horarias se gestionan en `/admin/users`. Cada usuario puede abrir `/admin/profile` para guardar idioma, tema, zona horaria, correo y contraseña. El enlace de recuperación en `/admin/login` usa tokens de un solo uso y respuestas que no revelan si un correo existe; configura SMTP en producción. Los intentos fallidos están limitados. En producción usa HTTPS y contraseñas fuertes y únicas. La autorización se basa en la sesión de usuario y permisos. Solo `administrator` y `operator` pueden modificar jugadores, ratings y categorías; `tournament_director` conserva las operaciones de torneos.
Los administradores pueden ajustar los intentos máximos de inicio de sesión, la ventana de limitación y la duración de los enlaces de recuperación en `/admin/settings`. Estos valores se guardan en SQLite y el botón de restauración usa los valores iniciales de `config.py`. `ADMIN_PASSWORD`, las rutas y las credenciales SMTP siguen siendo configuración del entorno.

## Hoja de ruta del proyecto

La hoja de ruta detallada y priorizada está en [FUTURE_FEATURES.md](FUTURE_FEATURES.md). La vista previa de importación con reconciliación explícita, los payloads tipados de OpenGotha, la revisión administrativa por cuenta con búsqueda de texto y filtros de fecha, la mejora del perfil del jugador y el modal explícito para eliminar torneos están implementados y verificados. Los perfiles incluyen historial reciente, rachas, torneos y filtro de temporada.

## Operaciones habituales

### Importar ratings y partidas

1. Inicia sesión en `/admin/login`.
2. Abre la pantalla de importación.
3. Carga uno de los formatos compatibles:
   - Libro `.xlsx` o `.xls`: importa los datos y reemplaza el conjunto de datos actual.
   - Archivo `.xml` de OpenGotha: importa partidas y metadatos del torneo.
   - Archivo `.csv` con las columnas `date`, `white`, `black` y `result`.
4. Confirma las posiciones y perfiles de jugadores resultantes.

Conserva una copia de seguridad antes de importar un libro que reemplace los datos.

### Gestionar un torneo

1. En administración, crea un torneo o importa un XML de OpenGotha.
2. Agrega participantes y elige suizo, suizo por categoría, suizo acelerado o McMahon.
3. Genera o administra manualmente los emparejamientos de cada ronda.
4. En la pantalla del torneo puedes editar el nombre, lugar, número de rondas, puntos de BYE y puntos de ausencia.
5. Registra resultados haciendo clic en el nombre del jugador ganador o en el texto del resultado. El texto recorre `-`, `1-0`, `1/2-1/2` y `0-1`; volver a hacer clic en el ganador lo deselecciona. El ganador queda resaltado en negrita y verde.
6. Registra descansos y ausencias, genera la siguiente ronda, revisa la clasificación y exporta los resultados con los botones de administración.

Las posiciones de la clasificación son siempre únicas y secuenciales; los empates se resuelven con SOS, SOSOS, SODOS, rating y nombre. El emparejamiento evita repetir el BYE en un mismo jugador mientras otro participante no lo haya recibido, y los BYE importados de OpenGotha quedan registrados para que las rondas futuras respeten ese historial.

Cuando una importación de OpenGotha encuentra un nombre parecido, muestra una sugerencia de un jugador en la base de datos. Haz clic en el nombre sugerido para vincularlo inmediatamente al jugador existente, o usa el selector para crear un jugador nuevo o elegir otro jugador.

### Consultar reportes

Abre `/reports` para elegir año, trimestre, mes, Todo el tiempo o un rango personalizado. La tabla muestra solo jugadores con partidas válidas en el periodo y permite abrir el rendimiento frente a cada oponente. También se muestran agregados por país y club del oponente. Los enlaces CSV y PDF conservan los filtros seleccionados y usan los mismos totales visibles en pantalla; el nombre del PDF incluye el jugador y el periodo.

Cuando se materializan resultados de ronda en la tabla principal de partidas, la columna `event` conserva el nombre del torneo o evento. La columna `notes` (visible como `Round` en la interfaz) guarda la etiqueta de la ronda en formato canónico, por ejemplo `Round 5`. Si la entrada esta en un formato de legado (por ej. `15:00:00`), se converva y convierte en ronda numérica. Si no se encuentra un valor numérico, se deja el texto y se trata como `0`.

Las tablas de torneos se migran automáticamente al iniciar para mantener la compatibilidad con bases de datos existentes.

Al recalcular ratings, el orden de las rondas se respeta dentro de cada día, tanto en el recálculo completo como en la actualización incremental. Si no se puede determinar la ronda, se usa la ronda 1.

### Revisar la auditoría administrativa

1. Inicia sesión en `/admin/login`.
2. Abre la pantalla de administración y usa la opción de auditoría.
3. Filtra por usuario o acción para revisar cambios en jugadores, partidas, ratings, importaciones, usuarios y configuraciones.

La pantalla de auditoría conserva el historial de actividad para cada cuenta y ayuda a revisar quién realizó cada cambio antes de tomar acciones de recuperación o soporte.

La bitácora registra las acciones administrativas que cambian el estado: importaciones, ciclo de vida y resultados de torneos, cambios de jugadores y partidas, ratings y categorías, usuarios y copias de seguridad. Guarda un resumen JSON compacto, limita los detalles a 2 KiB por evento y elimina por defecto las entradas con más de 730 días. Define `AUDIT_RETENTION_DAYS` antes de iniciar para usar otro periodo positivo.

### Copias de seguridad y restauración

Usa la pantalla de copias de seguridad antes de importaciones masivas, restauraciones o actualizaciones. El servidor genera y valida los nombres de los archivos de respaldo; los archivos restaurados pasan por la ruta de migración de la aplicación. La restauración reconstruye el índice de búsqueda de jugadores y solo considera copias generadas por la aplicación o el archivo `.bak` administrado; nunca usa archivos temporales de `data/`.

## Desarrollo

Ejecuta la suite de regresión desde la raíz del proyecto:

```powershell
pytest -q
```

Las pruebas cubren ratings y gráficos, filtros de jugadores, soporte de idiomas, respaldos, migraciones de torneos, emparejamientos, clasificación, compatibilidad con OpenGotha y páginas públicas de torneos.

La funcionalidad de ordenación, filtros y búsqueda consistente ya está entregada y validada en las páginas de jugadores, partidas y torneos.

## Próximas funciones recomendadas

1. **Mejoras de paginación.** Mostrar páginas totales, contexto de página actual y selección simple de resultados por página.
2. **Perfil de jugador con resultados y estadísticas de torneos.** Añadir en la ficha de cada jugador un resumen de torneos jugados, ratio de victorias/derrotas, resultados por evento, tabla de torneos recientes, rachas y porcentajes de rendimiento, con filtros por categoría y temporada.
3. **Estado simple de torneo oculto o no publicado.** Permitir excluir un torneo de la vista pública sin introducir un ciclo de vida completo.
4. **Copias programadas con retención y verificación de restauración.** Mantenerlas desactivadas por defecto y solo habilitarlas cuando exista política clara de retención.
5. **Flujo opcional de moderación de resultados.** Solo si el proceso del torneo lo requiere.

## Licencia y atribución

Revisa los archivos fuente y dependencias para conocer los detalles de licencia. La implementación de Glicko-2 fue desarrollada originalmente por Ryan Kirkman, publicado bajo la licencia MIT.
