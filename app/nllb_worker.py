# NLLB text translation runs on the assistant GPU, never the ASR GPU.
nllb_model=None
nllb_tokenizer=None

def translate_nllb(task):
    global nllb_model,nllb_tokenizer
    from transformers import AutoTokenizer,AutoModelForSeq2SeqLM
    codes={'vi':'vie_Latn','en':'eng_Latn'}
    source=task.get('source_language');target=task.get('target_language');units=task.get('units')
    if source not in codes or target not in codes or source==target:raise ValueError('Invalid translation direction')
    if not isinstance(units,list) or not 1<=len(units)<=4:raise ValueError('Invalid translation batch')
    if any(not isinstance(u,dict) or not isinstance(u.get('id'),str) or not isinstance(u.get('text'),str) or not 0<len(u['text'])<=450 for u in units):raise ValueError('Invalid translation unit')
    if len({u['id'] for u in units})!=len(units):raise ValueError('Duplicate translation ID')
    if torch.cuda.device_count()<2:raise ValueError('Live translation requires two GPUs')
    if nllb_model is None:
        print('Preparing NLLB English/Vietnamese translation on GPU 1...',flush=True)
        nllb_tokenizer=AutoTokenizer.from_pretrained('facebook/nllb-200-distilled-600M',token=HF_TOKEN)
        nllb_model=AutoModelForSeq2SeqLM.from_pretrained('facebook/nllb-200-distilled-600M',token=HF_TOKEN,torch_dtype=torch.float16).to('cuda:1').eval()
    nllb_tokenizer.src_lang=codes[source]
    inputs=nllb_tokenizer([u['text'] for u in units],return_tensors='pt',padding=True,truncation=False).to('cuda:1')
    if inputs.input_ids.shape[1]>512:raise ValueError('Translation input too long; source kept')
    from transformers import StoppingCriteria,StoppingCriteriaList
    class TranslationStopped(StoppingCriteria):
        def __call__(self,input_ids,scores,**kwargs):return stop.is_set()
    with torch.inference_mode():
        outputs=nllb_model.generate(**inputs,forced_bos_token_id=nllb_tokenizer.convert_tokens_to_ids(codes[target]),
            max_new_tokens=512,num_beams=2,do_sample=False,stopping_criteria=StoppingCriteriaList([TranslationStopped()]))
    if stop.is_set():raise RuntimeError('Translation cancelled')
    # Refuse visibly truncated generation instead of silently saving a partial sentence.
    if any(nllb_tokenizer.eos_token_id not in row[1:].tolist() for row in outputs):raise ValueError('Incomplete translation')
    texts=nllb_tokenizer.batch_decode(outputs,skip_special_tokens=True)
    if len(texts)!=len(units) or any(not t.strip() for t in texts):raise ValueError('Empty translation')
    return dict(job_id=task['job_id'],translations=[dict(id=u['id'],text=t.strip()) for u,t in zip(units,texts)],model='facebook/nllb-200-distilled-600M')
