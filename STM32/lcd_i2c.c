#include "lcd_i2c.h"

extern I2C_HandleTypeDef hi2c1;

#define LCD_ADDR 0x4E   // Change to 0x7E if your LCD is 0x3F
#define BACKLIGHT 0x08
#define ENABLE     0x04

void lcd_send_internal(char data, uint8_t flags)
{
    uint8_t high = data & 0xF0;
    uint8_t low  = (data << 4) & 0xF0;

    uint8_t data_arr[4];

    data_arr[0] = high | flags | ENABLE | BACKLIGHT;
    data_arr[1] = high | flags | BACKLIGHT;
    data_arr[2] = low  | flags | ENABLE | BACKLIGHT;
    data_arr[3] = low  | flags | BACKLIGHT;

    HAL_I2C_Master_Transmit(&hi2c1, LCD_ADDR, data_arr, 4, 100);
}

void lcd_send_cmd(char cmd)
{
    lcd_send_internal(cmd, 0x00);
}

void lcd_send_data(char data)
{
    lcd_send_internal(data, 0x01);
}

void lcd_init(void)
{
    HAL_Delay(50);

    lcd_send_cmd(0x33);
    lcd_send_cmd(0x32);
    lcd_send_cmd(0x28);
    lcd_send_cmd(0x0C);
    lcd_send_cmd(0x06);
    lcd_send_cmd(0x01);

    HAL_Delay(5);
}

void lcd_send_string(char *str)
{
    while(*str) lcd_send_data(*str++);
}

void lcd_clear(void)
{
    lcd_send_cmd(0x01);
    HAL_Delay(2);
}

void lcd_set_cursor(int row, int col)
{
    uint8_t addr = (row == 0) ? 0x80 + col : 0xC0 + col;
    lcd_send_cmd(addr);
}
