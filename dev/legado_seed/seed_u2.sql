-- Segunda unidade completa: e com duas que o vazamento aparece.
INSERT INTO input.unidade_regional VALUES ('u2','Litoral 2','r1','Norte',0.09);
INSERT INTO input.regional_superintendencia VALUES ('sup2','Sup B','u2');
INSERT INTO input.superintendencia_cidade VALUES ('c_niteroi','Niteroi','sup2');
INSERT INTO input.cidade_sistema VALUES ('s99','Sistema 99','c_niteroi');
INSERT INTO input.sistema_topologia VALUES ('b99_1','Sub 99.1','s99',NULL);
INSERT INTO input.sistema_topologia VALUES ('ete_s99','ETE 99','s99',NULL);
INSERT INTO input.subbacia_operacional (sub_bacia, preco_por_ligacao) VALUES ('b99_1', 100);
INSERT INTO input.ete_capex (ete_id, capacidade_por_modulo) VALUES ('ete_s99', 500);
INSERT INTO input.cts_operacional (cts, preco_por_ligacao) VALUES ('cts_u2', 50);
INSERT INTO input.subbacia_cts VALUES ('b99_1','cts_u2');
-- e a ETE/CTS da u1 entram na topologia dela
INSERT INTO input.sistema_topologia VALUES ('ete_s38','ETE 38','s38',NULL);
INSERT INTO input.cts_operacional (cts, preco_por_ligacao) VALUES ('cts_u1', 70);
INSERT INTO input.subbacia_cts VALUES ('b38_1','cts_u1');
