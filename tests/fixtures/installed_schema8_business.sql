-- Synthetic records only, for exercising the released schema-eight DDL.
INSERT INTO accounts(id,name,initial_equity,created,updated,current_total_assets)
VALUES('synthetic-account','Synthetic retained account',200000,'2026-10-30','2026-10-30',200000);
INSERT INTO positions(id,account_id,code,name,entry,stop,tp1,created,updated)
VALUES('synthetic-position','synthetic-account','sz.000001','Synthetic retained position',10,9,12,'2026-10-30','2026-10-30');
INSERT INTO position_fills(id,position_id,side,trade_date,price,quantity,fees,created)
VALUES('synthetic-fill','synthetic-position','BUY','2026-10-30',10,100,5,'2026-10-30');
INSERT INTO executions(id,account_id,code,side,trade_time,price,quantity,fee,created)
VALUES('synthetic-execution','synthetic-account','sz.000001','BUY','2026-10-30',10,100,5,'2026-10-30');
INSERT INTO closed_trades(id,account_id,code,name,close_date,holding_days,pnl,return_pct,created,updated)
VALUES('synthetic-closed','synthetic-account','sz.000002','Synthetic closed trade','2026-10-29',2,125,1.25,'2026-10-29','2026-10-29');
INSERT INTO cash_flows(id,account_id,flow_date,category,amount,notes,created,updated)
VALUES('synthetic-cash','synthetic-account','2026-10-29','deposit',1000,'Synthetic retained cash flow','2026-10-29','2026-10-29');
INSERT INTO scan_cache VALUES('synthetic-cache','{"retained":true}','2026-10-30');
INSERT INTO meta VALUES('synthetic-chart-document','{"drawings":[{"id":"synthetic-line"}]}');
