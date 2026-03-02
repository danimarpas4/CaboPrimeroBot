import json, random, os, logging, sqlite3, urllib.parse, asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, Poll
from telegram.ext import Application, CommandHandler, ContextTypes, PollHandler
from dotenv import load_dotenv

# Carga de variables de entorno
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = "@caboprimero" 
ZONA_ESP = ZoneInfo("Europe/Madrid")
FECHA_EXAMEN = datetime(2026, 6, 25, tzinfo=ZONA_ESP)

# Configuración de Logs
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- BASE DE DATOS ---
def init_db():
    conn = sqlite3.connect('stats.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS encuestas 
                      (poll_id TEXT PRIMARY KEY, materia TEXT, tema TEXT, aciertos INTEGER, 
                       total INTEGER, fecha TEXT, pregunta_texto TEXT)''')
    
    try:
        cursor.execute("SELECT materia FROM encuestas LIMIT 1")
    except sqlite3.OperationalError:
        logging.info("Migrando base de datos para añadir columna 'materia'...")
        cursor.execute("ALTER TABLE encuestas ADD COLUMN materia TEXT DEFAULT 'GENERAL'")
        
    conn.commit()
    conn.close()

init_db()

# --- CARGA DE PREGUNTAS ---
filename = 'preguntas_primero.json' if os.path.exists('preguntas_primero.json') else 'preguntas.json'
with open(filename, 'r', encoding='utf-8') as f:
    preguntas_oficiales = json.load(f)

# --- CONFIGURACIÓN DE DIFUSIÓN MULTIPLATAFORMA ---
url_privada = "https://t.me/addlist/q57lTY3FZTgwMzBk"
texto_compartir = (
    "¡Compañero! 🪖\n\nTe comparto esta comunidad de test gratuitos para preparar el ascenso a Cabo o a Cabo Primero. "
    "Preguntas oficiales cada hora, simulacros multi-materia y estadísticas reales.\n\n"
    f"Únete aquí: {url_privada}"
)

# Enlace para Telegram
url_tg_share = f"https://t.me/share/url?url={urllib.parse.quote(url_privada)}&text={urllib.parse.quote(texto_compartir.replace(url_privada, ''))}"
# Enlace para WhatsApp
url_wa_share = f"https://api.whatsapp.com/send?text={urllib.parse.quote(texto_compartir)}"

keyboard_viral = InlineKeyboardMarkup([
    [InlineKeyboardButton("✈️ COMPARTIR EN TELEGRAM", url=url_tg_share)],
    [InlineKeyboardButton("🟢 COMPARTIR EN WHATSAPP", url=url_wa_share)]
])

# --- OBTENER SALUDO ---
def obtener_saludo(es_simulacro=False):
    hoy = datetime.now(ZONA_ESP)
    dias = (FECHA_EXAMEN - hoy).days
    
    if dias > 0:
        mensaje = f"⏳ **CUENTA ATRÁS: Quedan {dias} días para el examen** 🎯\n\n"
    elif dias == 0:
        mensaje = f"🎯 **¡LLEGÓ EL DÍA!** 🎯\n\nEs el momento de demostrarlo todo. "
    else:
        mensaje = "🚀 **NUEVA CONVOCATORIA A CABO PRIMERO EN PREPARACIÓN** 🚀\n\n"

    if es_simulacro:
        mensaje += "🔥 **¡SIMULACRO MULTI-MATERIA!** 🔥\n"
        mensaje += "Ráfaga de 10 preguntas (Inglés, Geo, Info y Legislación). ¡Demuestra tu nivel!"
    else:
        mensaje += "🌅 **¡A por la jornada de instrucción!**"
        
    return mensaje

async def track_poll_results(update, context):
    poll = update.poll
    if poll.type != Poll.QUIZ or poll.correct_option_id is None: return

    aciertos = next((o.voter_count for o in poll.options if o.voter_count and poll.options.index(o) == poll.correct_option_id), 0)
    
    conn = sqlite3.connect('stats.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE encuestas SET aciertos = ?, total = ? WHERE poll_id = ?", 
                   (aciertos, poll.total_voter_count, poll.id))
    conn.commit()
    conn.close()

def preparar_texto_informe():
    hoy = datetime.now(ZONA_ESP).strftime('%Y-%m-%d')
    conn = sqlite3.connect('stats.db')
    cursor = conn.cursor()
    cursor.execute("SELECT materia, tema, SUM(aciertos), SUM(total) FROM encuestas WHERE fecha = ? GROUP BY materia, tema", (hoy,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows: return None
    
    total_respuestas = sum(r[3] for r in rows)
    if total_respuestas == 0: return None
    
    total_aciertos = sum(r[2] for r in rows)
    precision_global = (total_aciertos / total_respuestas) * 100
    
    informe = f"📊 **PARTE DE NOVEDADES - {datetime.now(ZONA_ESP).strftime('%d/%m/%Y')}** 📊\n\n"
    informe += f"🎯 **Rendimiento Global de la Unidad:** `{precision_global:.1f}%` ({total_aciertos}/{total_respuestas})\n\n"
    
    materias_dict = {}
    for r in rows:
        m, t, ac, tot = r
        if m not in materias_dict: materias_dict[m] = []
        materias_dict[m].append((t, ac, tot))
    
    for mat, temas in materias_dict.items():
        informe += f"🔹 **{mat.upper()}**\n"
        for t_nombre, t_ac, t_tot in temas:
            t_perc = (t_ac / t_tot * 100) if t_tot > 0 else 0
            icono = "🟢" if t_perc >= 75 else "🟡" if t_perc >= 50 else "🔴"
            informe += f"   {icono} {t_nombre}: `{t_perc:.1f}%`\n"
        informe += "\n"
        
    informe += "Descansen. Mañana continuamos la instrucción. 🪖"
    return informe

async def informe_arsenal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != 113333060: return
    informe = preparar_texto_informe()
    await update.message.reply_text(informe or "Sin datos de actividad hoy.", parse_mode="Markdown")

# --- LANZAMIENTO DE TANDA ---
async def lanzar_tanda(bot, cantidad, es_simulacro=False, enviar_cierre=True):
    hoy = datetime.now(ZONA_ESP)
    hace_7_dias = (hoy - timedelta(days=7)).strftime('%Y-%m-%d')
    conn = sqlite3.connect('stats.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT DISTINCT pregunta_texto FROM encuestas WHERE fecha >= ?", (hace_7_dias,))
    recientes = [row[0] for row in cursor.fetchall()]
    pool = [p for p in preguntas_oficiales if p['pregunta'] not in recientes]
    
    if len(pool) < cantidad: pool = list(preguntas_oficiales)
    random.shuffle(pool)

    await bot.send_message(chat_id=CHAT_ID, text=obtener_saludo(es_simulacro), reply_markup=keyboard_viral, parse_mode="Markdown")

    enviadas = 0
    for p in pool:
        if enviadas >= cantidad: break
        
        materia_label = p.get('materia', 'LEGISLACIÓN').upper()
        tema_label = p.get('titulo_tema', 'GENERAL').upper()
        
        try:
            question_text = f"📜 [{materia_label}] {tema_label}\n\n{p['pregunta']}"[:300]
            
            msg = await bot.send_poll(
                CHAT_ID, 
                question=question_text, 
                options=[str(o)[:100] for o in p['opciones']], 
                type='quiz', 
                correct_option_id=int(p['correcta']), 
                explanation=f"{p.get('explicacion','')}"[:190],
                is_anonymous=True,
                question_parse_mode="Markdown"
            )
            
            cursor.execute("INSERT INTO encuestas VALUES (?, ?, ?, ?, ?, ?, ?)", 
                           (msg.poll.id, materia_label, tema_label, 0, 0, hoy.strftime('%Y-%m-%d'), p['pregunta']))
            conn.commit()
            enviadas += 1
        except Exception as e:
            logging.error(f"Fallo enviando encuesta: {e}")

    conn.close()
    if enviar_cierre:
        msg_cierre = "✅ **INSTRUCCIÓN FINALIZADA**\n\nNo dejes a tus compañeros atrás. Comparte el canal para ayudar a la unidad. 👇"
        await bot.send_message(chat_id=CHAT_ID, text=msg_cierre, reply_markup=keyboard_viral, parse_mode="Markdown")

# --- PROGRAMACIÓN ---
async def enviar_batch_automatico(context):
    ahora = datetime.now(ZONA_ESP)
    if ahora.date() == FECHA_EXAMEN.date(): return
    if not (6 <= ahora.hour <= 22): return 
    
    es_finde = ahora.weekday() >= 5  
    if es_finde and ahora.hour not in [10, 14, 18, 22]: return
    
    cantidad = 10 if es_finde else 2
    await lanzar_tanda(context.bot, cantidad, es_simulacro=es_finde)

async def cierre_jornada(context):
    await lanzar_tanda(context.bot, 2, es_simulacro=False, enviar_cierre=False)
    await asyncio.sleep(2)
    informe = preparar_texto_informe()
    await context.bot.send_message(chat_id=CHAT_ID, text=informe or "Sin actividad hoy.", parse_mode="Markdown")
    await asyncio.sleep(2)
    msg_footer = "✅ **PARTE DE NOVEDADES FINALIZADO**\n\nComparte el canal con tu unidad para reforzar el estudio conjunto. 👇"
    await context.bot.send_message(chat_id=CHAT_ID, text=msg_footer, reply_markup=keyboard_viral, parse_mode="Markdown")

def main():
    app = Application.builder().token(TOKEN).build()
    ahora = datetime.now(ZONA_ESP)
    segundos_hasta_en_punto = 3600 - (ahora.minute * 60 + ahora.second)
    
    app.job_queue.run_repeating(enviar_batch_automatico, interval=3600, first=segundos_hasta_en_punto)
    app.job_queue.run_daily(cierre_jornada, time=time(23, 0, tzinfo=ZONA_ESP))

    app.add_handler(CommandHandler("disparar", lambda u, c: lanzar_tanda(c.bot, 2, False)))
    app.add_handler(CommandHandler("arsenal", informe_arsenal))
    app.add_handler(PollHandler(track_poll_results))
    
    print("🚀 Bot Cabo Primero con Difusión Multiplataforma activo.")
    app.run_polling()

if __name__ == '__main__': main()