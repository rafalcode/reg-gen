import numpy as np

map = {'A':0, 'C':1, 'G':2, 'T':3}

class LSlim:

    def __init__(self, length, distance):
        self.length = length
        self.distance = distance

        # parameters
        self.prob_c, self.prob_c0, self.prob_c1, self.prob_c1_m = self.init()

    def init(self):
        # probabilities of independent or conditional depending on previous positions
        prob_c = np.random.dirichlet(np.ones(2), size=self.length-1)
        prob_c0 = np.random.dirichlet(np.ones(4), size=self.length)
        prob_c1 = np.zeros((self.length-1, 4, 4))
        prob_c1_m = list()

        conditional_params = list()
        for k in range(0, self.length-1):
            prob_c1[k] = np.random.dirichlet(np.ones(4), size=4)

        for k in range(1, self.length):
            if k < self.distance:
                prob_c1_m.append(np.random.dirichlet(np.ones(k), size=1).tolist()[0])
            else:
                prob_c1_m.append(np.random.dirichlet(np.ones(self.distance), size=1).tolist()[0])

        return prob_c, prob_c0, prob_c1, prob_c1_m


    #def loglikelihood(self):

    # def likelihood(self, x):
    #     vec = self.seq2vec(x)
    #     prob = 1.0
    #
    #     for k in range(self.length):
    #         prob *= self.prob_c[k][0] * self.prob_c0[k][vec[k]] + prob_c1[k]
    #
    #         p = 0.0
    #         for m in range(min(self.distance, k)):
    #             p +=  self.prob_c1[k][vec[k]] * self.prob_c1_m[k][k-m][vec[k]]
    #
    # def fit(self, X):
    #
    # def seq2vec(self, seq):
    #     vec = list()
    #     for s in seq:
    #         vec.append(map[s])
    #     return vec