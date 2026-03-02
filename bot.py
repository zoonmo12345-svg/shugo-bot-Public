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

# DB
conn = sqlite3.connect('prices.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS prices
             (id INTEGER PRIMARY KEY,
              item_name TEXT,
              price REAL,
              timestamp TEXT)''')
conn.commit()

# 수식 계산 함수
def parse_number(text: str) -> int:
    text = text.replace(" ", "")
    if not re.match(r'^[0-9+\-*/().]+$', text):
        return int(text)
    try:
        result = eval(text, {"__builtins__": {}}, {})
        return int(result) if result == int(result) else int(round(result))
    except:
        return int(text)

@client.event
async def on_ready():
    await tree.sync()
    print(f'{client.user} 상인단 차트봇 ON (재료비 OR 기능 개선됨)')

# ==================== 마진 계산기 ====================
class MarginModal(ui.Modal, title="마진 계산 입력 - 재료비는 하나당 OR 총재료비 중 하나만 입력해달라거~!"):
    material_cost_per = ui.TextInput(
        label="하나당 재료비 (키나)",
        placeholder="예: 5000 또는 1000+1200*3",
        style=discord.TextStyle.short,
        required=False
    )
    total_material_input = ui.TextInput(
        label="총 재료비 (키나)",
        placeholder="예: 15000000 또는 5000*3000",
        style=discord.TextStyle.short,
        required=False
    )
    sale_price = ui.TextInput(
        label="판매 희망가 (키나)",
        placeholder="예: 1000000 또는 500000*2",
        style=discord.TextStyle.short,
        required=True
    )
    craft_count = ui.TextInput(
        label="제작할 개수",
        placeholder="예: 100 또는 50+20",
        style=discord.TextStyle.short,
        required=True,
        default="100"
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            craft_count = parse_number(self.craft_count.value)
            sale_price = parse_number(self.sale_price.value)

            per = self.material_cost_per.value.strip()
            total_str = self.total_material_input.value.strip()

            if not per and not total_str:
                await interaction.response.send_message("❌ **하나당 재료비** 또는 **총 재료비** 중 **하나만** 입력해달라거~!", ephemeral=True)
                return
            if per and total_str:
                await interaction.response.send_message("❌ **하나당 재료비**와 **총 재료비**를 **둘 다 입력하지 마**! 하나만 넣어달라거~!", ephemeral=True)
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

        # 계산 로직 (기존 그대로)
        net_per_sale = sale_price * 0.8
        effective_net = sale_price * 0.78

        breakeven = -(-total_material // effective_net)
        breakeven_minus1 = breakeven - 1
        profit_minus1 = (breakeven_minus1 * net_per_sale) - (total_material + (sale_price * breakeven_minus1 * 0.02))
        profit_breakeven = (breakeven * net_per_sale) - (total_material + (sale_price * breakeven * 0.02))

        embed = discord.Embed(title="🛠 마진 계산 결과", color=discord.Color.blue())
        embed.add_field(name="📊 제작 계획", value=f"• 제작할 개수: **{craft_count}개**\n• 판매 희망가: **{sale_price:,} 키나**", inline=False)
        embed.add_field(name="💰 투자 정보", value=f"• 총 재료비: **{total_material:,} 키나**", inline=False)
        embed.add_field(name="🔥 손익분기점 (22% 수수료 포함)", value=f"**최소 {breakeven}개 성공**해야 본전 + 수익 시작\n\n- {breakeven_minus1}개 성공 → **{profit_minus1:,} 키나** (여전히 손해)\n- {breakeven}개 성공 → **{profit_breakeven:,} 키나** (이익 시작)", inline=False)

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
        embed.add_field(name="📈 수익", value=f"• 총 실수령액 (20% 수수료 후): **{net_revenue:,} 키나**", inline=False)
        embed.add_field(name="💎 최종 순이익", value=f"+{profit:,} 키나\n**마진률: {margin_rate:.1f}%**", inline=False)

        await interaction.response.send_message(embed=embed)

# /마진계산 명령어
@tree.command(name="마진계산", description="아이온2 마진 계산기")
async def margin(interaction: discord.Interaction):
    modal = MarginModal()
    await interaction.response.send_modal(modal)

# ==================== 기존 기능들 (기록, 차트, 차트수정) ====================
# (여기에 기존 /기록, /차트, /차트수정 코드 그대로 넣으면 됨. 길어서 생략했지만 이전에 준 거 그대로 복붙하면 끝)

client.run(TOKEN)
