INSERT INTO input.unidade_regional VALUES ('u1','Litoral 1','r1','Norte',0.095);
INSERT INTO input.regional_superintendencia VALUES ('sup1','Sup A','u1');
INSERT INTO input.superintendencia_cidade VALUES ('c_rio','Rio Bonito','sup1');
INSERT INTO input.cidade_sistema VALUES ('s38','Sistema 38','c_rio');
INSERT INTO input.sistema_topologia (componente_sistema_id, componente_sistema_nome, sistema_id, componente_sistema_id_jusante) VALUES ('b38_1','Sub-bacia 38.1','s38',NULL);
INSERT INTO input.cidade_operacional (cidade_id, unidade_cobertura) VALUES ('c_rio','ligacoes');
INSERT INTO input.subbacia_operacional (sub_bacia, preco_por_ligacao, universo_ligacoes, ligacoes_atuais) VALUES ('b38_1', 1850, 300, 100);
INSERT INTO input.metas_cobertura VALUES ('c_rio', 2030, 0.4);
INSERT INTO input.fator_esgoto VALUES ('c_rio','Rio Bonito', 0.4, 0.72);
-- A ETE precisa estar na TOPOLOGIA, e nao so em `ete_capex`: e por
-- `sistema_topologia` que ela chega a uma unidade (o motor a identifica assim,
-- `otimizador_capex_v62.py:1111`). Sem esta linha ela existe e nao pertence a
-- ninguem — e o `PUT /etes` responde 404, corretamente.
INSERT INTO input.sistema_topologia (componente_sistema_id, componente_sistema_nome, sistema_id, componente_sistema_id_jusante) VALUES ('ete_s38','ETE 38','s38',NULL);
INSERT INTO input.ete_capex (ete_id, capacidade_por_modulo, capex_por_modulo) VALUES ('ete_s38', 270, 137000000);

-- uma rodada publicada, com o minimo para os 11 endpoints de leitura
INSERT INTO public.otim_meta (run_id, data_hora, regional, anos_capex, ano_base, orcamento_total,
  params_extra, milp_status, vpl, capex_total, opex_total, receita_total, obras_total,
  obras_construidas, obrig_total, obrig_construidas, subbacias_total, subbacias_faturando,
  metas_total, metas_nao_atingidas, cobertura_final_pct, rotulo, usuario, tempo_s, status_execucao)
VALUES ('run_teste_1', now(), 'u1', 8, 2026, 410000000,
  '{"BASE_RECEITA":"arrecadada","USAR_CTS":true,"FOCO_COBERTURA":1.0,"INCLUIR_INDUSTRIAL":true}'::jsonb,
  'VIAVEL(limite de tempo)', 168069034, 304182900, 81440200, 469000000, 902, 367, 3, 3, 902, 218,
  2, 0, 77.6, 'Litoral 1 — janela 8a', 'lucio.rosa', 274, 'CONCLUIDO');
INSERT INTO public.otim_cidade (run_id,cidade,sub_bacias,obras_feitas,obras_fora,capex_total,vpl,
  ligacoes_novas,cobertura_base_pct,cobertura_final_pct,metas_total,metas_atingidas,
  paridade_inicial,paridade_final,unidade_cobertura)
VALUES ('run_teste_1','Rio Bonito',6,12,3,48900000,69100000,12480,31.8,77.6,2,2,0.6,0.72,'ligacoes');
INSERT INTO public.otim_sistema (run_id,sistema,cidade,ano_fim_concessao,sub_bacias,sub_bacias_faturando,
  ete_id,capacidade_instalada,vazao_conectada,ocupacao_pct,vazao_nao_atendida,capex_modulos_construidos)
VALUES ('run_teste_1','Sistema 38','Rio Bonito',2049,6,4,'ete_s38',270,209.7,77.7,102.2,11344500);
INSERT INTO public.otim_subbacia (run_id,sub_bacia,cidade,sistema,jusante,is_cts,vazao_marginal,
  faturando,vpl,vp_capex_rateado,vp_opex_rateado,vp_receita_direta,vp_receita_indireta,vp_efeito_base,
  pot_vp_receita,pot_vp_capex_solo,pot_vp_opex,pot_saldo_solo,pot_saldo_rateado,motivo_sem_receita)
VALUES ('run_teste_1','b38_1','Rio Bonito','Sistema 38',NULL,false,41.2,true,168069034,304182900,
  81440200,469000000,38000000,46692134,0,0,0,0,0,NULL);
INSERT INTO public.otim_obra (run_id,obra_id,componente,no,sistema,cidade,is_cts,responsavel,capex,
  quantidade,unidade,preco_unitario,opex_ano,prazo_meses,inicio_min_mes,obrigatoria,ligacoes,
  ticket_mes,preco_ligacao,wacc,wacc_origem,data_inicio,data_pronta,construida,status,
  categoria_motivo,motivo,elo_que_trava)
VALUES ('run_teste_1','lig_b38_1','Ligação de esgoto','b38_1','Sistema 38','Rio Bonito',false,
  'Aegea',180000,80,'un',1850,5580,6,12,true,2530,84.7,71.15,0.0945,'proprio','2027-03','2028-09',
  true,'DENTRO',NULL,NULL,NULL);
INSERT INTO public.otim_dependencia VALUES ('run_teste_1','lig_b38_1','coleta','b38_1','Rio Bonito',
  'Sistema 38',41.2,41.2,1.0,180000,1,true,true);
INSERT INTO public.otim_ano VALUES ('run_teste_1',2026,0,48000000,1660000,0,0,0,-1660000,-1660000,NULL,52000000,92.3,0,true);
INSERT INTO public.otim_mes VALUES ('run_teste_1',0,2026,1,'2026-01',4000000,4000000);
INSERT INTO public.otim_subbacia_ano VALUES ('run_teste_1','b38_1','Rio Bonito','Sistema 38',2030,477859,312400,0,0,100000,690259,true);
INSERT INTO public.otim_cidade_ano VALUES ('run_teste_1','Rio Bonito',2026,48000000);
INSERT INTO public.otim_cobertura VALUES ('run_teste_1','Rio Bonito',2026,100,300,31.8);
INSERT INTO public.otim_meta_cobertura VALUES ('run_teste_1','Rio Bonito',2030,0.4,400,524,0,true,true);
INSERT INTO public.otim_paridade VALUES ('run_teste_1','Rio Bonito',2030,0.72,0.6,0.12);
