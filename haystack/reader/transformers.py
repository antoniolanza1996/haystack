from typing import List, Optional

from haystack.reader.transformers_utils import pipeline

from haystack.database.base import Document
from haystack.reader.base import BaseReader


class TransformersReader(BaseReader):
    """
    Transformer based model for extractive Question Answering using the huggingface's transformers framework
    (https://github.com/huggingface/transformers).
    While the underlying model can vary (BERT, Roberta, DistilBERT ...) the interface remains the same.

    With the reader, you can:
     - directly get predictions via predict()
    """

    def __init__(
        self,
        model: str = "distilbert-base-uncased-distilled-squad",
        tokenizer: Optional[str] = None,
        context_window_size: int = 70,
        use_gpu: int = 0,
        top_k_per_candidate: int = 4,
        return_no_answers: bool = True,
        max_seq_len: int = 256,
        doc_stride: int = 128
    ):
        """
        Load a QA model from Transformers.
        Available models include:
        - distilbert-base-uncased-distilled-squad
        - bert-large-cased-whole-word-masking-finetuned-squad
        - bert-large-uncased-whole-word-masking-finetuned-squad

        See https://huggingface.co/models for full list of available QA models

        :param model: name of the model
        :param tokenizer: name of the tokenizer (usually the same as model)
        :param context_window_size: num of chars (before and after the answer) to return as "context" for each answer.
                            The context usually helps users to understand if the answer really makes sense.
        :param use_gpu: < 0  -> use cpu
                        >= 0 -> ordinal of the gpu to use
        :param top_k_per_candidate: How many answers to extract for each candidate doc that is coming from the retriever (might be a long text).
                                           Note: - This is not the number of "final answers" you will receive
                                           (see `top_k` in TransformersReader.predict() or Finder.get_answers() for that)
                                         - Can includes no_answer in the sorted list of predictions
        :param return_no_answers: True -> Hugging Face model could return an "impossible"/"empty" answer (i.e. when there is an unanswerable question)
                                  False -> otherwise
                                  no_answer_boost is unfortunately not available with TransformersReader. If you would like to
                                  set no_answer_boost, use a FARMReader
        :param max_seq_len: max sequence length of one input text for the model
        :param doc_stride: length of striding window for splitting long texts (used if len(text) > max_seq_len)

        """
        self.model = pipeline('question-answering', model=model, tokenizer=tokenizer, device=use_gpu)
        self.context_window_size = context_window_size
        self.top_k_per_candidate = top_k_per_candidate
        self.return_no_answers = return_no_answers
        self.max_seq_len = max_seq_len
        self.doc_stride = doc_stride

        # TODO context_window_size behaviour different from behavior in FARMReader

    def predict(self, question: str, documents: List[Document], top_k: Optional[int] = None):
        """
        Use loaded QA model to find answers for a question in the supplied list of Document.

        Returns dictionaries containing answers sorted by (desc.) probability
        Example:
        {'question': 'Who is the father of Arya Stark?',
        'answers': [
                     {'answer': 'Eddard,',
                     'context': " She travels with her father, Eddard, to King's Landing when he is ",
                     'offset_answer_start': 147,
                     'offset_answer_end': 154,
                     'probability': 0.9787139466668613,
                     'score': None,
                     'document_id': None
                     },
                    ...
                   ]
        }

        :param question: question string
        :param documents: list of Document in which to search for the answer
        :param top_k: the maximum number of answers to return
        :return: dict containing question and answers

        """
        # get top-answers for each candidate passage
        answers = []
        no_ans_gaps = []
        best_overall_score = 0
        for doc in documents:
            query = {"context": doc.text, "question": question}
            predictions = self.model(query,
                                     topk=self.top_k_per_candidate,
                                     handle_impossible_answer=self.return_no_answers,
                                     max_seq_len=self.max_seq_len,
                                     doc_stride=self.doc_stride)
            # for single preds (e.g. via top_k=1) transformers returns a dict instead of a list
            if type(predictions) == dict:
                predictions = [predictions]
            # assemble and format all answers

            best_doc_score = 0
            # because we cannot ensure a "no answer" prediction coming back from transformers we initialize it here with 0
            no_ans_doc_score = 0
            # TODO add no answer bias on haystack side after getting "no answer" scores from transformers
            for pred in predictions:
                if pred["answer"]:
                    if pred["score"] > best_doc_score:
                        best_doc_score = pred["score"]
                    context_start = max(0, pred["start"] - self.context_window_size)
                    context_end = min(len(doc.text), pred["end"] + self.context_window_size)
                    answers.append({
                        "answer": pred["answer"],
                        "context": doc.text[context_start:context_end],
                        "offset_start": pred["start"],
                        "offset_end": pred["end"],
                        "probability": pred["score"],
                        "score": None,
                        "document_id": doc.id,
                        "meta": doc.meta
                    })
                else:
                    no_ans_doc_score = pred["score"]

                if best_doc_score > best_overall_score:
                    best_overall_score = best_doc_score

            no_ans_gaps.append(no_ans_doc_score - best_doc_score)

        # Calculate the score for predicting "no answer", relative to our best positive answer score
        no_ans_prediction, max_no_ans_gap = self._calc_no_answer(no_ans_gaps, best_overall_score)

        if self.return_no_answers:
            answers.append(no_ans_prediction)
        # sort answers by their `probability` and select top-k
        answers = sorted(
            answers, key=lambda k: k["probability"], reverse=True
        )
        answers = answers[:top_k]

        results = {"question": question,
                   "answers": answers}

        return results

    def predict_batch(self, question_doc_list: List[dict], top_k_per_question: Optional[int] = None,
                      batch_size: Optional[int] = None):

        raise NotImplementedError("Batch prediction not yet available in TransformersReader.")

    def predict_on_texts(self, question: str, texts: List[str], top_k: Optional[int] = None):
        documents = []
        for text in texts:
            documents.append(
                Document(
                    text=text
                )
            )
        predictions = self.predict(question, documents, top_k)
        return predictions