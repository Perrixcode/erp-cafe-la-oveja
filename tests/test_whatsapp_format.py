"""Contrato de texto exacto. Fixtures ficticios, sin persistencia ni envíos."""
from copy import deepcopy
import unittest
from erp.domain import whatsapp,stock_summary,date_range,summarize


def order(customer,day,flavor,size,hour='12:30',channel='Presencial',status='pendiente',quantity=1):
    return {'customer':customer,'pickup_at':day+'T'+hour,'channel':channel,'fulfillment':'retiro','is_demo':True,
            'items':[{'flavor':flavor,'size':size,'status':status,'quantity':quantity,'kind':'torta'}]}


def fixture():
    orders=[order('Cliente ficticio Tres','2026-10-02','Amapola','20 personas','15:00'),
            order('Cliente ficticio Dos','2026-09-30','Trufa manjar','10 personas'),
            order('Cliente ficticio Uno','2026-09-29','Trufa manjar','20 personas','17:00','Instagram')]
    stock=stock_summary([{'flavor':'Amapola','size':'20 personas','physical':4,'reserved':0},
                         {'flavor':'Trufa manjar','size':'10 personas','physical':0,'reserved':0},
                         {'flavor':'Trufa manjar','size':'20 personas','physical':2,'reserved':1}])
    return orders,stock

EXPECTED='''PRODUCTOS POR MARCAR;
SEMANA: 28/09 - 02/10

*LUNES 28/09*

“”

*MARTES 29/09*

Trufa manjar *20PP* - Cliente ficticio Uno - 17:00 hrs - IG

*MIERCOLES 30/09*

Trufa manjar *10PP* - Cliente ficticio Dos - 12:30 hrs - Local

*JUEVES 01/10*

“”

*VIERNES 02/10*

Amapola *20PP* - Cliente ficticio Tres - 15:00 hrs - Local

RESUMEN ENCARGADAS

Trufa manjar *20PP*: 1
Trufa manjar *10PP*: 1
Amapola *20PP*: 1

STOCK DISPONIBLE:

Trufa manjar *20PP*: 1**
Trufa manjar *10PP*: 0
Amapola *20PP*: 4

** = Reservada para trozo'''


class WhatsAppFormatTests(unittest.TestCase):
    def test_exact_template_whitespace_punctuation_asterisks_and_order(self):
        orders,stock=fixture();before=deepcopy((orders,stock))
        self.assertEqual(whatsapp(orders,'2026-09-28','2026-10-02',stock),EXPECTED)
        self.assertEqual((orders,stock),before)

    def test_dates_are_dynamic_weekends_and_all_empty_days_included(self):
        start,end=date_range('2027-01-02','week')
        text=whatsapp([],start,end)
        self.assertEqual((start,end),('2026-12-28','2027-01-03'))
        expected=['*LUNES 28/12*','*MARTES 29/12*','*MIERCOLES 30/12*','*JUEVES 31/12*','*VIERNES 01/01*','*SABADO 02/01*','*DOMINGO 03/01*']
        self.assertEqual([line for line in text.splitlines() if line.startswith('*') and not line.startswith('**')],expected)
        self.assertEqual(text.count('“”'),7)
        self.assertTrue(text.startswith('PRODUCTOS POR MARCAR;\nSEMANA: 28/12 - 03/01\n'))

    def test_quantities_statuses_channel_not_fulfillment_and_summary_preserved(self):
        rows=[order('Pendiente ficticio','2026-10-05','Chocolate','10 personas',quantity=2,channel='Instagram'),
              order('Solicitado ficticio','2026-10-05','Chocolate','10 personas',status='marcado_solicitado'),
              order('Marcado ficticio','2026-10-05','Vainilla','20 personas',status='marcado',quantity=3),
              order('Entregado ficticio','2026-10-05','Excluir entregado','20 personas',status='entregado'),
              order('Cancelado ficticio','2026-10-05','Excluir cancelado','20 personas',status='cancelado')]
        rows[0]['fulfillment']='despacho'
        text=whatsapp(rows,'2026-10-05','2026-10-05')
        marking,summary=text.split('RESUMEN ENCARGADAS')
        self.assertEqual(marking.count('Chocolate *10PP* - Pendiente ficticio - 12:30 hrs - IG'),2)
        self.assertIn('Solicitado ficticio',marking);self.assertNotIn('Marcado ficticio',marking)
        self.assertNotIn('Excluir',text);self.assertIn('Chocolate *10PP*: 3',summary);self.assertIn('Vainilla *20PP*: 3',summary)
        self.assertEqual(summarize(rows)['outstanding'],6)

    def test_stock_unknown_zero_and_reservation_use_actual_values_without_double_subtract(self):
        stock=stock_summary([{'flavor':'Chocolate','size':'10 personas','physical':None,'reserved':0},
                             {'flavor':'Vainilla','size':'20 personas','physical':0,'reserved':0},
                             {'flavor':'Frambuesa','size':'20 personas','physical':7,'reserved':3}])
        text=whatsapp([order('Cliente ficticio','2026-10-05','Frambuesa','20 personas',quantity=2)],'2026-10-05','2026-10-05',stock)
        self.assertIn('Chocolate *10PP*: Desconocido',text);self.assertIn('Vainilla *20PP*: 0\n',text)
        self.assertIn('Frambuesa *20PP*: 4**',text)
        self.assertNotIn('Frambuesa *20PP*: 2**',text)
        self.assertEqual(stock['rows'][2]['reserved'],3)

    def test_selected_period_limits_orders_and_keeps_unknown_format(self):
        rows=[order('Fuera ficticio','2026-09-30','Excluido','20 personas'),
              order('Dentro ficticio','2026-10-01','Kuchen','Entero · tamaño no confirmado',channel='Web/Mercat')]
        text=whatsapp(rows,'2026-10-01','2026-10-01')
        self.assertNotIn('Fuera ficticio',text);self.assertNotIn('Excluido',text)
        self.assertIn('Kuchen *Entero · tamaño no confirmado* - Dentro ficticio - 12:30 hrs - Web',text)
        self.assertIn('Desconocido · no se proporcionó stock físico.',text)
        self.assertNotIn('PP',text)

    def test_time_sorting_and_month_range_keep_every_calendar_day(self):
        rows=[order('Tarde ficticio','2026-11-02','Chocolate','20 personas','19:00'),
              order('Temprano ficticio','2026-11-02','Chocolate','20 personas','10:00')]
        start,end=date_range('2026-11-15','month');text=whatsapp(rows,start,end)
        self.assertEqual(text.count('“”'),29)
        self.assertLess(text.index('Temprano ficticio'),text.index('Tarde ficticio'))
        self.assertIn('*DOMINGO 01/11*',text);self.assertIn('*LUNES 30/11*',text)
