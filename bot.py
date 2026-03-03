import discord
from discord.ext import commands
from discord import ui, app_commands
import matplotlib.pyplot as plt
import sqlite3
from datetime import datetime, timedelta
import io
import os
from dotenv import load_dotenv
import pandas as pd
from matplotlib.ticker import FuncFormatter
import re

# ==================== 한글 폰트 ====================
plt.rcParams['font.family'] = 'Malgun Gothic'
plt.rcParams['axes.unicode_minus'] = False

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ==================== DB ====================
conn = sqlite3.connect('prices.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS prices
             (id INTEGER PRIMARY KEY,
              item_name TEXT,
              price REAL,
              timestamp TEXT)''')
conn.commit()

# ==================== 수식 계산 함수 ====================
def parse_number(text: str) -> int:
    text = text.replace(" ", "")
    if not re.match(r'^[0-9+\-*/().]+$', text):
        return int(text)
    try:
        result = eval(text, {"__builtins__": {}}, {})
        return int(result) if result == int(result) else int(round(result))
    except:
        return int(text)

# ==================== on_ready (v1.8 표시) ====================
@client.event
async def on_ready():
    await tree.sync(guild=None)   # 전체 서버 동기화
    print(f'{client.user} 상인단 차트봇 ON - v1.8 (마진계산기 + 차트 전부 합체 완료)')
    print("=== v1.8 버전 적용됨 ===")

# ==================== 차트 기능 ====================
def price_formatter(x, pos):
    if x >= 100_000_000:
        return f'{x/100_000_000:.1f}억'
    elif x >= 10_000:
        return f'{x/10_000:.0f}만'
    else:
        return f'{int(x):,}'

@tree.command(name="기록", description="아이템 가격 기록")
async def add_price(interaction: discord.Interaction, 아이템: str, 가격: float):
    now = datetime.now().isoformat()
    c.execute("INSERT INTO prices (item_name, price, timestamp) VALUES (?, ?, ?)", (아이템, 가격, now))
    c.execute("SELECT COUNT(*) FROM prices WHERE item_name=?", (아이템,))
    count = c.fetchone()[0]
    if count > 200:
        c.execute("DELETE FROM prices WHERE id IN (SELECT id FROM prices WHERE item_name=? ORDER BY timestamp ASC LIMIT ?)", (아이템, count-200))
    conn.commit()
    await interaction.response.send_message(f"✅ {아이템} {가격:,}키나 기록 완료!")

@tree.command(name="차트", description="아이템 가격 추이 차트")
@app_commands.describe(아이템="아이템 이름")
async def show_chart(interaction: discord.Interaction, 아이템: str, 봉타입: str = "일봉"):
    await interaction.response.defer()
    
    valid_types = ["분봉", "시간봉", "일봉", "월봉"]
    if 봉타입 not in valid_types:
        봉타입 = "일봉"
    
    valid_timeframes = {'분봉': 'min', '시간봉': 'h', '일봉': 'D', '월봉': 'ME'}
    
    c.execute("SELECT timestamp, price FROM prices WHERE item_name=? ORDER BY timestamp ASC", (아이템,))
    data = c.fetchall()
    
    if not data:
        await interaction.followup.send("❌ 기록된 가격 없음")
        return
    
    df = pd.DataFrame(data, columns=['timestamp', 'price'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    
    resampled = df.resample(valid_timeframes[봉타입]).agg({'price': ['first', 'max', 'min', 'last']})
    resampled.columns = ['open', 'high', 'low', 'close']
    resampled = resampled.dropna()
    
    if resampled.empty:
        await interaction.followup.send("❌ 데이터 부족")
        return
    
    plt.figure(figsize=(12, 7))
    plt.plot(resampled.index, resampled['close'], marker='o', linewidth=2.5, color='#0066ff', label='Close Price')
    plt.fill_between(resampled.index, resampled['low'], resampled['high'], color='gray', alpha=0.25)
    plt.gca().yaxis.set_major_formatter(FuncFormatter(price_formatter))
    plt.title(f'{아이템} 가격 추이 ({봉타입})', fontsize=14, pad=20)
    plt.xlabel('시간')
    plt.ylabel('가격 (키나)')
    plt.grid(True, alpha=0.3)
    plt.xticks(rotation=45)
    plt.legend(fontsize=11)
    plt.tight_layout()
    
    for i in range(max(0, len(resampled)-15), len(resampled)):
        price = resampled['close'].iloc[i]
        plt.annotate(f'{int(price):,}', (resampled.index[i], price), textcoords="offset points", xytext=(0, 12), ha='center', fontsize=9, color='#0066ff', fontweight='bold')
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=220, bbox_inches='tight')
    buf.seek(0)
    plt.close()
    
    file = discord.File(buf, filename=f"{아이템}_chart.png")
    embed = discord.Embed(title=f"{아이템} {봉타입} 차트", color=0x00ff00)
    embed.set_image(url="attachment://" + f"{아이템}_chart.png")
    
    await interaction.followup.send(embed=embed, file=file)

@tree.command(name="차트수정", description="잘못된 가격 삭제")
@app_commands.checks.has_permissions(administrator=True)
async def delete_price(interaction: discord.Interaction, 아이템: str, 가격: float, 날짜시간: str):
    try:
        dt = datetime.strptime(날짜시간, '%Y-%m-%d-%H-%M')
    except ValueError:
        await interaction.response.send_message("❌ 형식: yyyy-mm-dd-hh-mm")
        return
    start = dt - timedelta(minutes=1)
    end = dt + timedelta(minutes=1)
    c.execute("DELETE FROM prices WHERE item_name=? AND price=? AND timestamp BETWEEN ? AND ?", (아이템, 가격, start.isoformat(), end.isoformat()))
    conn.commit()
    await interaction.response.send_message(f"✅ {c.rowcount}개 데이터 삭제 완료!")

# ==================== 마진 계산기 ====================
class MarginModal(ui.Modal, title="마진 계산 입력 - 재료비는 하나당 OR 총재료비 중 하나만 입력해달라거~!"):
    material_cost_per = ui.TextInput(label="하나당 재료비 (키나)", placeholder="예: 5000 또는 1000+1200*3", style=discord.TextStyle.short, required=False)
    total_material_input = ui.TextInput(label="총 재료비 (키나)", placeholder="예: 15000000 또는 5000*3000", style=discord.TextStyle.short, required=False)
    sale_price = ui.TextInput(label="판매 희망가 (키나)", placeholder="예: 1000000 또는 500000*2", style=discord.TextStyle.short, required=True)
    craft_count = ui.TextInput(label="제작할 개수", placeholder="예: 100 또는 50+20", style=discord.TextStyle.short, required=True, default="100")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            craft_count = parse_number(self.craft_count.value)
            sale_price = parse_number(self.sale_price.value)

            per = self.material_cost_per.value.strip()
            total_str = self.total_material_input.value.strip()

            if not per and not total_str:
                await interaction.response.send_message("❌ 하나당 재료비 또는 총 재료비 중 **하나만** 입력해달라거~!", ephemeral=True)
                return
            if per and total_str:
                await interaction.response.send_message("❌ 하나당과 총 재료비 **둘 다 넣지 마**! 하나만!", ephemeral=True)
                return

            if per:
                material_cost = parse_number(per)
            else:
                total_material = parse_number(total_str)
                material_cost = total_material // craft_count

            total_material = material_cost * craft_count

        except ValueError:
            await interaction.response.send_message("숫자나 수식만 입력해달라거~!", ephemeral=True)
            return

        net_per_sale = sale_price * 0.8
        effective_net = sale_price * 0.78

        breakeven = -(-total_material // effective_net)
        breakeven_minus1 = breakeven - 1
        profit_minus1 = (breakeven_minus1 * net_per_sale) - (total_material + (sale_price * breakeven_minus1 * 0.02))
        profit_breakeven = (breakeven * net_per_sale) - (total_material + (sale_price * breakeven * 0.02))

        embed = discord.Embed(title="🛠 마진 계산 결과", color=discord.Color.blue())
        embed.add_field(name="📊 제작 계획", value=f"• 제작할 개수: **{craft_count}개**\n• 판매 희망가: **{sale_price:,} 키나**", inline=False)
        embed.add_field(name="💰 투자 정보", value=f"• 총 재료비: **{total_material:,} 키나**", inline=False)
        embed.add_field(name="🔥 손익분기점 (22% 수수료 포함)", value=f"**최소 {breakeven}개 성공**해야 본전 + 수익 시작\n\n- {breakeven_minus1}개 성공 → **{profit_minus1:,} 키나** (손해)\n- {breakeven}개 성공 → **{profit_breakeven:,} 키나** (이익 시작)", inline=False)

        view = ui.View()
        button = ui.Button(label="최종 순이익 계산하기", style=discord.ButtonStyle.primary)
        button.callback = lambda i: self.profit_modal(i, material_cost, sale_price, craft_count, total_material)
        view.add_item(button)

        await interaction.response.send_message(embed=embed, view=view)

    async def profit_modal(self, interaction: discord.Interaction, material_cost, sale_price, craft_count, total_material):
        modal = ProfitModal(material_cost, sale_price, craft_count, total_material)
        await interaction.response.send_modal(modal)

class ProfitModal(ui.Modal, title="최종 순이익 입력"):
    success_count = ui.TextInput(label="성공한 개수 ← 수식 OK", style=discord.TextStyle.short, required=True)

    def __init__(self, material_cost, sale_price, craft_count, total_material):
        super().__init__()
        self.material_cost = material_cost
        self.sale_price = sale_price
        self.craft_count = craft_count
        self.total_material = total_material

    async def on_submit(self, interaction: discord.Interaction):
        try:
            success_count = parse_number(self.success_count.value)
        except ValueError:
            await interaction.response.send_message("숫자나 수식만 입력해달라거~!", ephemeral=True)
            return

        reg_fee = self.sale_price * success_count * 0.02
        total_invest = self.total_material + reg_fee
        net_revenue = self.sale_price * success_count * 0.8
        profit = net_revenue - total_invest
        margin_rate = (profit / total_invest) * 100 if total_invest > 0 else 0

        embed = discord.Embed(title="✅ 최종 순이익 계산 완료", color=discord.Color.green())
        embed.add_field(name="📊 결과 요약", value=f"• 성공 개수: **{success_count}개**\n• 판매 희망가: **{self.sale_price:,} 키나**", inline=False)
        embed.add_field(name="💰 비용", value=f"• 총 재료비: **{self.total_material:,} 키나**\n• 등록수수료 (2%): **{reg_fee:,} 키나**\n• **총 투자금: {total_invest:,} 키나**", inline=False)
        embed.add_field(name="📈 수익", value=f"• 총 실수령액: **{net_revenue:,} 키나**", inline=False)
        embed.add_field(name="💎 최종 순이익", value=f"+{profit:,} 키나\n**마진률: {margin_rate:.1f}%**", inline=False)

        await interaction.response.send_message(embed=embed)

# /마진계산 명령어
@tree.command(name="마진계산", description="아이온2 마진 계산기")
async def margin(interaction: discord.Interaction):
    modal = MarginModal()
    await interaction.response.send_modal(modal)

client.run(TOKEN)
