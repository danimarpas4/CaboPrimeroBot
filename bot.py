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
                      (poll_id TEXT PRIMARY KEY, tema TEXT, aciertos INTEGER, 
                       total INTEGER, fecha TEXT, pregunta_texto TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- CARGA DE PREGUNTAS ---
with open('preguntas__primero.json', 'r', encoding='utf-8') as f:
    preguntas_oficiales = json.load(f)

# --- CONFIGURACIÓN DE COMPARTIR CORREGIDA ---
url_privada = "https://t.me/addlist/q57lTY3FZTgwMzBk"
# Eliminamos la URL del texto porque Telegram la concatena automáticamente al final de este string
texto_compartir = (
    "¡Compañero! 🪖\n\nTe comparto este canal de test gratuitos para preparar el ascenso a Cabo y Cabo Primero. "
    "Preguntas oficiales cada hora, simulacros, cuenta atrás para el examen y estadísticas.\n\n"
    "Únete al canal y prepárate en condiciones:"
)
url_tg_share = f"https://t.me/share/url?url={urllib.parse.quote(url_privada)}&text={urllib.parse.quote(texto_compartir)}"
keyboard_viral = InlineKeyboardMarkup([
    [InlineKeyboardButton("⚔️ COMPARTIR CON TUS COMPAÑEROS ⚔️", url=url_tg_share)]
])

# --- OBTENER SALUDO CON AVISO DE SIMULACRO ---
def obtener_saludo(es_simulacro=False):
    hoy = datetime.now(ZONA_ESP)
    dias = (FECHA_EXAMEN - hoy).days
    
    # Base del mensaje
    if dias > 0:
        mensaje = f"⏳ **CUENTA ATRÁS: Quedan {dias} días para el examen** 🎯\n\n"
    elif dias == 0:
        mensaje = f"🎯 **¡LLEGÓ EL DÍA!** 🎯\n\nEs el momento de demostrarlo todo. "
    else:
        mensaje = "🚀 **NUEVA CONVOCATORIA A CABO PRIMERO EN PREPARACIÓN** 🚀\n\n"

    # Añadido especial si es simulacro de fin de semana
    if es_simulacro:
        mensaje += "🔥 **¡SIMULACRO DE FIN DE SEMANA!** 🔥\n"
        mensaje += "Ráfaga de 10 preguntas. ¡Demuestra tu nivel, aspirante!"
    else:
        mensaje += "🌅 **¡A por la jornada, aspirante!**"
        
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
    cursor.execute("SELECT tema, SUM(aciertos), SUM(total) FROM encuestas WHERE fecha = ? GROUP BY tema", (hoy,))
    stats = cursor.fetchall()
    conn.close()
    
    if not stats: return None
    
    total_respuestas = sum(t[2] for t in stats)
    if total_respuestas == 0: return None
    
    total_aciertos = sum(t[1] for t in stats)
    precision_global = (total_aciertos / total_respuestas) * 100
    
    informe = f"📊 **PARTE DE NOVEDADES - {datetime.now(ZONA_ESP).strftime('%d/%m/%Y')}** 📊\n\n"
    informe += f"🎯 **Rendimiento Global de la Unidad:** `{precision_global:.1f}%` ({total_aciertos}/{total_respuestas} aciertos)\n\n"
    
    for stat in stats:
        t_aciertos, t_total = stat[1], stat[2]
        t_porcentaje = (t_aciertos / t_total * 100) if t_total > 0 else 0
        icono = "🟢" if t_porcentaje >= 75 else "🟡" if t_porcentaje >= 50 else "🔴"
        informe += f"{icono} *{stat[0]}*: `{t_porcentaje:.1f}%`\n"
        
    informe += "\nDescansen. Mañana continuamos la instrucción. 🪖"
    return informe

async def informe_arsenal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != 113333060: return
    informe = preparar_texto_informe()
    await update.message.reply_text(informe or "Sin datos.", parse_mode="Markdown")

async def enviar_mensaje_examen(context):
    msg = ("🔥 **¡HA LLEGADO EL DÍA, ASPIRANTES!** 🔥\n\n"
           "Todo el sudor, las horas restadas al sueño y el esfuerzo de estos meses se reducen a este momento.\n\n"
           "Confiad en vuestra preparación. Leed bien cada pregunta. No hay atajos, solo disciplina y determinación.\n\n"
           "**¡Mucha fuerza a todos y a por esa plaza!** 🪖🇪🇸")
    await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

# --- LANZAMIENTO DE TANDA CON BUCLE ANTI-ERRORES ---
# Añadimos un parámetro enviar_cierre para controlarlo desde fuera
async def lanzar_tanda(bot, cantidad, es_simulacro=False, enviar_cierre=True):
    hoy = datetime.now(ZONA_ESP)
    hace_7_dias = (hoy - timedelta(days=7)).strftime('%Y-%m-%d')
    conn = sqlite3.connect('stats.db')
    cursor = conn.cursor()
    
    # Filtro para no repetir preguntas de la última semana
    cursor.execute("SELECT DISTINCT pregunta_texto FROM encuestas WHERE fecha >= ?", (hace_7_dias,))
    recientes = [row[0] for row in cursor.fetchall()]
    pool = [p for p in preguntas_oficiales if p['pregunta'] not in recientes]
    
    if len(pool) < cantidad:
        pool = list(preguntas_oficiales)

    await bot.send_message(chat_id=CHAT_ID, text=obtener_saludo(es_simulacro), reply_markup=keyboard_viral, parse_mode="Markdown")

    enviadas = 0
    intentos = 0
    max_intentos = cantidad * 5  
    
    random.shuffle(pool)

    while enviadas < cantidad and intentos < max_intentos and pool:
        p = pool.pop(0)  
        intentos += 1
        success = False
        
        explicacion_intento = f"{p.get('explicacion','')}"[:190]
        
        try:
            msg = await bot.send_poll(
                CHAT_ID, 
                question=f"📜 [{p.get('titulo_tema','').upper()}]\n\n{p['pregunta']}"[:300], 
                options=[str(o)[:100] for o in p['opciones']], 
                type='quiz', 
                correct_option_id=int(p['correcta']), 
                explanation=explicacion_intento,
                is_anonymous=True,
                question_parse_mode="Markdown",
                explanation_parse_mode="Markdown"
            )
            success = True
        except Exception as e:
            if "Explanation_too_long" in str(e) or "Poll_explanation_length" in str(e):
                logging.info(f"⚠️ Explicación pesada. Reintentando con recorte de seguridad a 150...")
                try:
                    explicacion_intento = f"{p.get('explicacion','')}"[:150]
                    msg = await bot.send_poll(
                        CHAT_ID, 
                        question=f"📜 [{p.get('titulo_tema','').upper()}]\n\n{p['pregunta']}"[:300], 
                        options=[str(o)[:100] for o in p['opciones']], 
                        type='quiz', 
                        correct_option_id=int(p['correcta']), 
                        explanation=explicacion_intento,
                        is_anonymous=True,
                        question_parse_mode="Markdown",
                        explanation_parse_mode="Markdown"
                    )
                    success = True
                except Exception as e2:
                    logging.error(f"❌ Fallo crítico en pregunta: {e2}")
            else:
                logging.error(f"❌ Error de otro tipo: {e}")

        if success:
            enviadas += 1
            cursor.execute("INSERT INTO encuestas VALUES (?, ?, ?, ?, ?, ?)", 
                           (msg.poll.id, p.get('titulo_tema','').upper(), 0, 0, hoy.strftime('%Y-%m-%d'), p['pregunta']))
            conn.commit()
            
    conn.close()
    
    # Solo envía el mensaje genérico de cierre si no es el final de la jornada
    if enviar_cierre:
        msg_cierre = "✅ **ENTRENAMIENTO FINALIZADO**\n\nNo dejes a tus compañeros atrás. Comparte el canal para ayudarnos entre nosotros. 👇"
        await bot.send_message(chat_id=CHAT_ID, text=msg_cierre, reply_markup=keyboard_viral, parse_mode="Markdown")

# --- PROGRAMACIÓN DE TAREAS ---
async def enviar_batch_automatico(context):
    ahora = datetime.now(ZONA_ESP)
    if ahora.date() == FECHA_EXAMEN.date(): return
    if not (6 <= ahora.hour <= 22): return 
    
    es_finde = ahora.weekday() >= 5  
    
    if es_finde:
        if ahora.hour in [10, 14, 18, 22]:
            cantidad = 10
            es_simulacro = True
        else:
            return  
    else:
        cantidad = 2
        es_simulacro = False
        
    await lanzar_tanda(context.bot, cantidad, es_simulacro, enviar_cierre=True)

async def cierre_jornada(context):
    ahora = datetime.now(ZONA_ESP)
    if ahora.date() == FECHA_EXAMEN.date(): return
    
    es_finde = ahora.weekday() >= 5
    cantidad = 10 if es_finde else 2
    es_simulacro = es_finde
    
    # 1. Lanzamos las preguntas, pero le decimos que NO envíe el mensaje de cierre todavía
    await lanzar_tanda(context.bot, cantidad, es_simulacro, enviar_cierre=False)
    await asyncio.sleep(2)
    
    # 2. Enviamos el parte de novedades (estadísticas de la jornada)
    informe = preparar_texto_informe()
    await context.bot.send_message(chat_id=CHAT_ID, text=informe or "Hoy no ha habido actividad registrada.", parse_mode="Markdown")
    
    # 3. Y para rematar, enviamos SIEMPRE el botón de compartir
    await asyncio.sleep(2)
    msg_cierre = "✅ **PARTE DE NOVEDADES FINALIZADO**\n\nNo dejes a tus compañeros atrás. Comparte el canal para ayudarnos entre nosotros. 👇"
    await context.bot.send_message(chat_id=CHAT_ID, text=msg_cierre, reply_markup=keyboard_viral, parse_mode="Markdown")

def main():
    app = Application.builder().token(TOKEN).build()
    ahora = datetime.now(ZONA_ESP)
    
    segundos_hasta_en_punto = 3600 - (ahora.minute * 60 + ahora.second)
    app.job_queue.run_repeating(enviar_batch_automatico, interval=3600, first=segundos_hasta_en_punto)
    
    # Cierre de jornada 23:00
    app.job_queue.run_daily(cierre_jornada, time=time(23, 0, tzinfo=ZONA_ESP))

    # Mensaje motivación día examen
    fecha_motivacion = datetime(2026, 2, 25, 7, 0, tzinfo=ZONA_ESP)
    if ahora < fecha_motivacion:
        app.job_queue.run_once(enviar_mensaje_examen, when=fecha_motivacion)
    
    # Para forzar un disparo manual (usa 2 preguntas por defecto)
    app.add_handler(CommandHandler("disparar", lambda u, c: lanzar_tanda(c.bot, 2, False)))
    app.add_handler(CommandHandler("test_cierre", lambda u, c: cierre_jornada(c)))
    app.add_handler(CommandHandler("arsenal", informe_arsenal))
    app.add_handler(PollHandler(track_poll_results))
    
    print("🚀 Bot en guardia. Lógica de compartir unificada y corregida.")
    app.run_polling()

if __name__ == '__main__': main()